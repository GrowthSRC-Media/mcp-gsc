from typing import Any, Dict, List, Optional
import logging
import os
import json
import secrets
import contextvars
import re
from datetime import datetime, timedelta

# Load .env before reading any os.environ values
from dotenv import load_dotenv
load_dotenv()

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route, Mount
from starlette.middleware.base import BaseHTTPMiddleware

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Allow OAuth over plain http in local/dev mode (when SERVER_URL is http://)
if (os.environ.get("SERVER_URL") or "").startswith("http://"):
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

# ─── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("gsc-mcp")

# Suppress noisy third-party loggers
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
logging.getLogger("googleapiclient.discovery").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

from mcp.server.sse import SseServerTransport
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gsc-server")

# ─── Config (all values come from .env) ────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.environ.get("DATA_DIR") or SCRIPT_DIR
os.makedirs(DATA_DIR, exist_ok=True)  # ensure data dir exists before any writes

SERVER_URL = (os.environ.get("SERVER_URL") or "http://localhost:8080").rstrip("/")
PORT = int(os.environ.get("PORT") or 8080)
MCP_TRANSPORT = (os.environ.get("MCP_TRANSPORT") or "sse").lower()

OAUTH_CLIENT_ID = os.environ.get("GSC_OAUTH_CLIENT_ID") or ""
OAUTH_CLIENT_SECRET = os.environ.get("GSC_OAUTH_CLIENT_SECRET") or ""
OAUTH_PROJECT_ID = os.environ.get("GSC_OAUTH_PROJECT_ID") or ""
OAUTH_AUTH_URI = (
    os.environ.get("GSC_OAUTH_AUTH_URI")
    or "https://accounts.google.com/o/oauth2/auth"
)
OAUTH_TOKEN_URI = (
    os.environ.get("GSC_OAUTH_TOKEN_URI")
    or "https://oauth2.googleapis.com/token"
)
OAUTH_AUTH_PROVIDER_CERT_URL = (
    os.environ.get("GSC_OAUTH_AUTH_PROVIDER_CERT_URL")
    or "https://www.googleapis.com/oauth2/v1/certs"
)
OAUTH_JAVASCRIPT_ORIGINS = os.environ.get("GSC_OAUTH_JAVASCRIPT_ORIGINS") or ""

REDIRECT_URI = f"{SERVER_URL}/oauth/callback"


def _build_web_client_config() -> dict:
    """Build the OAuth client config dict (web app) from env vars — mirrors client_secrets.json structure."""
    config: dict = {
        "client_id": OAUTH_CLIENT_ID,
        "client_secret": OAUTH_CLIENT_SECRET,
        "auth_uri": OAUTH_AUTH_URI,
        "token_uri": OAUTH_TOKEN_URI,
        "auth_provider_x509_cert_url": OAUTH_AUTH_PROVIDER_CERT_URL,
        "redirect_uris": [REDIRECT_URI],
    }
    if OAUTH_PROJECT_ID:
        config["project_id"] = OAUTH_PROJECT_ID
    if OAUTH_JAVASCRIPT_ORIGINS:
        config["javascript_origins"] = [OAUTH_JAVASCRIPT_ORIGINS]
    return {"web": config}


def _build_installed_client_config() -> dict:
    """Build the OAuth client config dict (desktop/installed app) from env vars — mirrors client_secrets.json structure."""
    config: dict = {
        "client_id": OAUTH_CLIENT_ID,
        "client_secret": OAUTH_CLIENT_SECRET,
        "auth_uri": OAUTH_AUTH_URI,
        "token_uri": OAUTH_TOKEN_URI,
        "auth_provider_x509_cert_url": OAUTH_AUTH_PROVIDER_CERT_URL,
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
    }
    if OAUTH_PROJECT_ID:
        config["project_id"] = OAUTH_PROJECT_ID
    return {"installed": config}

_raw_data_state = os.environ.get("GSC_DATA_STATE", "all").lower().strip()
if _raw_data_state not in ("all", "final"):
    raise ValueError(
        f"Invalid GSC_DATA_STATE value '{_raw_data_state}'. "
        "Accepted values are 'all' (default, matches GSC dashboard) or 'final' (2-3 day lag)."
    )
DATA_STATE = _raw_data_state

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

# ─── Per-request user context ──────────────────────────────────────────────────

# Holds the current user's API key for the duration of an MCP request.
current_user_key: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_user_key", default=None
)


# ─── Token / file helpers ──────────────────────────────────────────────────────

def _tokens_dir() -> str:
    d = os.path.join(DATA_DIR, "tokens")
    os.makedirs(d, exist_ok=True)
    return d


def _sanitize_key(key: str) -> str:
    """Allow only alphanumeric, dash, underscore to prevent path traversal."""
    return re.sub(r"[^A-Za-z0-9_\-]", "", key)


def _token_file(user_key: str) -> str:
    return os.path.join(_tokens_dir(), f"{_sanitize_key(user_key)}.json")


# ─── OAuth state persistence ───────────────────────────────────────────────────
# States are written to disk so they survive a server restart mid-flow.

_STATES_FILE = os.path.join(DATA_DIR, "oauth_states.json")
_STATE_TTL_SECONDS = 600  # 10 minutes


def _load_states() -> dict:
    if not os.path.exists(_STATES_FILE):
        return {}
    try:
        with open(_STATES_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_states(states: dict) -> None:
    with open(_STATES_FILE, "w") as f:
        json.dump(states, f)


def _create_oauth_state(user_key: str) -> str:
    state = secrets.token_urlsafe(32)
    states = _load_states()
    # Prune expired states
    now = datetime.utcnow().timestamp()
    states = {k: v for k, v in states.items()
               if now - v.get("ts", 0) < _STATE_TTL_SECONDS}
    states[state] = {"user_key": user_key, "ts": now}
    _save_states(states)
    return state


def _consume_oauth_state(state: str) -> Optional[dict]:
    """Return the full state entry and remove it, or None if invalid/expired."""
    states = _load_states()
    entry = states.pop(state, None)
    _save_states(states)
    if not entry:
        return None
    age = datetime.utcnow().timestamp() - entry.get("ts", 0)
    if age > _STATE_TTL_SECONDS:
        return None
    return entry


# ─── Auth ──────────────────────────────────────────────────────────────────────

def get_gsc_service():
    """
    Returns an authorized Search Console service object.

    In SSE/multi-tenant mode: uses the current request's API key to locate
    the user's stored OAuth token.

    In stdio mode: falls back to local OAuth flow or service account (legacy).
    """
    if MCP_TRANSPORT == "stdio":
        return _get_gsc_service_stdio()

    user_key = current_user_key.get()
    if not user_key:
        raise RuntimeError(
            "No API key found in this connection. "
            "Make sure you configured your Claude Desktop with ?key=<your-api-key>."
        )
    return _get_gsc_service_for_key(user_key)


def _get_gsc_service_for_key(user_key: str):
    """Load (or refresh) credentials for a specific user key."""
    token_file = _token_file(user_key)
    creds = None

    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception:
            logger.warning("Token file for key ...%s is corrupt — deleting", user_key[-6:])
            os.remove(token_file)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                logger.info("Refreshing expired token for key ...%s", user_key[-6:])
                creds.refresh(GoogleRequest())
                with open(token_file, "w") as f:
                    f.write(creds.to_json())
                logger.info("Token refreshed successfully for key ...%s", user_key[-6:])
            except Exception as e:
                logger.warning("Token refresh failed for key ...%s: %s", user_key[-6:], e)
                if os.path.exists(token_file):
                    os.remove(token_file)
                creds = None

        if not creds or not creds.valid:
            logger.warning("No valid credentials for key ...%s — re-auth required", user_key[-6:])
            raise RuntimeError(
                f"Your Google authorization has expired or was never completed.\n\n"
                f"Please visit this URL to re-authorize:\n\n"
                f"  {SERVER_URL}/setup?key={user_key}\n\n"
                f"Make sure you are signed into the correct Google account in your browser first."
            )

    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


# ─── Legacy stdio auth (unchanged from original) ───────────────────────────────

# Path to service account / OAuth token for local/stdio mode
GSC_CREDENTIALS_PATH = os.environ.get("GSC_CREDENTIALS_PATH")
POSSIBLE_CREDENTIAL_PATHS = [
    GSC_CREDENTIALS_PATH,
    os.path.join(DATA_DIR, "service_account_credentials.json"),
    os.path.join(SCRIPT_DIR, "service_account_credentials.json"),
]
TOKEN_FILE = os.path.join(DATA_DIR, "token.json")
SKIP_OAUTH = (os.environ.get("GSC_SKIP_OAUTH") or "").lower() in ("true", "1", "yes")


def _get_gsc_service_stdio():
    if not SKIP_OAUTH:
        try:
            return _get_gsc_service_oauth_stdio()
        except Exception as e:
            print(f"OAuth authentication failed: {str(e)}")

    for cred_path in POSSIBLE_CREDENTIAL_PATHS:
        if cred_path and os.path.exists(cred_path):
            try:
                creds = service_account.Credentials.from_service_account_file(
                    cred_path, scopes=SCOPES
                )
                return build("searchconsole", "v1", credentials=creds, cache_discovery=False)
            except Exception:
                continue

    raise FileNotFoundError(
        "Authentication failed. Please either:\n"
        "1. Set GSC_OAUTH_CLIENT_ID and GSC_OAUTH_CLIENT_SECRET in your .env file, or\n"
        "2. Set the GSC_CREDENTIALS_PATH environment variable or place a service account "
        "credentials file in one of these locations: "
        f"{', '.join([p for p in POSSIBLE_CREDENTIAL_PATHS[1:] if p])}"
    )


def _get_gsc_service_oauth_stdio():
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleRequest())
                with open(TOKEN_FILE, "w") as token:
                    token.write(creds.to_json())
            except Exception:
                if os.path.exists(TOKEN_FILE):
                    os.remove(TOKEN_FILE)
                creds = None

        if not creds or not creds.valid:
            if not OAUTH_CLIENT_ID or not OAUTH_CLIENT_SECRET:
                raise RuntimeError(
                    "GSC_OAUTH_CLIENT_ID and GSC_OAUTH_CLIENT_SECRET must be set in .env."
                )
            flow = InstalledAppFlow.from_client_config(_build_installed_client_config(), SCOPES)
            creds = flow.run_local_server(port=8080)
            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())

    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


# ─── Shared helpers ────────────────────────────────────────────────────────────

def _site_not_found_error(site_url: str) -> str:
    lines = [f"Property '{site_url}' not found (404). Possible causes:\n"]
    lines.append(
        "1. The site_url doesn't exactly match what is in GSC. "
        "Run list_properties to get the exact string to use."
    )
    if site_url.startswith("sc-domain:"):
        lines.append(
            "2. Domain properties require the service account to be explicitly added "
            "under GSC Settings > Users and permissions for that specific domain property. "
            "OAuth users must also have verified access to it."
        )
    else:
        lines.append(
            "2. If your property is a domain property (covers all subdomains), "
            "the correct format is 'sc-domain:example.com', not a full URL."
        )
    lines.append("3. The authenticated account may not have access to this property.")
    return "\n".join(lines)


# ─── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def list_properties() -> str:
    """
    Retrieves and returns the user's Search Console properties.
    """
    try:
        service = get_gsc_service()
        site_list = service.sites().list().execute()
        sites = site_list.get("siteEntry", [])

        if not sites:
            return "No Search Console properties found."

        lines = []
        for site in sites:
            site_url = site.get("siteUrl", "Unknown")
            permission = site.get("permissionLevel", "Unknown permission")
            lines.append(f"- {site_url} ({permission})")

        return "\n".join(lines)
    except FileNotFoundError as e:
        return (
            "Error: Service account credentials file not found.\n\n"
            "To access Google Search Console, please:\n"
            "1. Create a service account in Google Cloud Console\n"
            "2. Download the JSON credentials file\n"
            "3. Save it as 'service_account_credentials.json' in the same directory as this script\n"
            "4. Share your GSC properties with the service account email"
        )
    except Exception as e:
        return f"Error retrieving properties: {str(e)}"


@mcp.tool()
async def add_site(site_url: str) -> str:
    """
    Add a site to your Search Console properties.

    Args:
        site_url: The URL of the site to add (must be exact match e.g. https://example.com, or https://www.example.com, or https://subdomain.example.com/path/, for domain properties use format: sc-domain:example.com)
    """
    try:
        service = get_gsc_service()
        response = service.sites().add(siteUrl=site_url).execute()

        result_lines = [f"Site {site_url} has been added to Search Console."]
        if "permissionLevel" in response:
            result_lines.append(f"Permission level: {response['permissionLevel']}")
        return "\n".join(result_lines)
    except HttpError as e:
        error_content = json.loads(e.content.decode("utf-8"))
        error_details = error_content.get("error", {})
        error_code = e.resp.status
        error_message = error_details.get("message", str(e))
        error_reason = error_details.get("errors", [{}])[0].get("reason", "")

        if error_code == 409:
            return f"Site {site_url} is already added to Search Console."
        elif error_code == 403:
            if error_reason == "forbidden":
                return "Error: You don't have permission to add this site. Please verify ownership first."
            elif error_reason == "quotaExceeded":
                return "Error: API quota exceeded. Please try again later."
            else:
                return f"Error: Permission denied. {error_message}"
        elif error_code == 400:
            if error_reason == "invalidParameter":
                return "Error: Invalid site URL format. Please check the URL format and try again."
            else:
                return f"Error: Bad request. {error_message}"
        elif error_code == 401:
            return "Error: Unauthorized. Please check your credentials."
        elif error_code == 429:
            return "Error: Too many requests. Please try again later."
        elif error_code == 500:
            return "Error: Internal server error from Google Search Console API. Please try again later."
        elif error_code == 503:
            return "Error: Service unavailable. Google Search Console API is currently down. Please try again later."
        else:
            return f"Error adding site (HTTP {error_code}): {error_message}"
    except Exception as e:
        return f"Error adding site: {str(e)}"


@mcp.tool()
async def get_search_analytics(site_url: str, days: int = 28, dimensions: str = "query", row_limit: int = 20) -> str:
    """
    Get search analytics data for a specific property.

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
        days: Number of days to look back (default: 28)
        dimensions: Dimensions to group by (default: query). Options: query, page, device, country, date
                   You can provide multiple dimensions separated by comma (e.g., "query,page")
        row_limit: Number of rows to return (default: 20, max: 500). Use 5-20 for quick overviews,
                   50-200 for deeper analysis, up to 500 for comprehensive reports. For bulk exports
                   beyond 500 rows, use get_advanced_search_analytics which supports pagination.
    """
    try:
        service = get_gsc_service()

        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)
        dimension_list = [d.strip() for d in dimensions.split(",")]

        request = {
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate": end_date.strftime("%Y-%m-%d"),
            "dimensions": dimension_list,
            "rowLimit": min(max(1, row_limit), 500),
            "dataState": DATA_STATE,
        }

        response = service.searchanalytics().query(siteUrl=site_url, body=request).execute()

        if not response.get("rows"):
            return f"No search analytics data found for {site_url} in the last {days} days."

        result_lines = [f"Search analytics for {site_url} (last {days} days):"]
        result_lines.append("\n" + "-" * 80 + "\n")

        header = [dim.capitalize() for dim in dimension_list]
        header.extend(["Clicks", "Impressions", "CTR", "Position"])
        result_lines.append(" | ".join(header))
        result_lines.append("-" * 80)

        for row in response.get("rows", []):
            data = [dim_value[:100] for dim_value in row.get("keys", [])]
            data.append(str(row.get("clicks", 0)))
            data.append(str(row.get("impressions", 0)))
            data.append(f"{row.get('ctr', 0) * 100:.2f}%")
            data.append(f"{row.get('position', 0):.1f}")
            result_lines.append(" | ".join(data))

        return "\n".join(result_lines)
    except Exception as e:
        if "404" in str(e):
            return _site_not_found_error(site_url)
        return f"Error retrieving search analytics: {str(e)}"


@mcp.tool()
async def get_site_details(site_url: str) -> str:
    """
    Get detailed information about a specific Search Console property.

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
    """
    try:
        service = get_gsc_service()
        site_info = service.sites().get(siteUrl=site_url).execute()

        result_lines = [f"Site details for {site_url}:", "-" * 50]
        result_lines.append(f"Permission level: {site_info.get('permissionLevel', 'Unknown')}")

        if "siteVerificationInfo" in site_info:
            verify_info = site_info["siteVerificationInfo"]
            result_lines.append(f"Verification state: {verify_info.get('verificationState', 'Unknown')}")
            if "verifiedUser" in verify_info:
                result_lines.append(f"Verified by: {verify_info['verifiedUser']}")
            if "verificationMethod" in verify_info:
                result_lines.append(f"Verification method: {verify_info['verificationMethod']}")

        if "ownershipInfo" in site_info:
            owner_info = site_info["ownershipInfo"]
            result_lines.append("\nOwnership Information:")
            result_lines.append(f"Owner: {owner_info.get('owner', 'Unknown')}")
            if "verificationMethod" in owner_info:
                result_lines.append(f"Ownership verification: {owner_info['verificationMethod']}")

        return "\n".join(result_lines)
    except Exception as e:
        return f"Error retrieving site details: {str(e)}"


@mcp.tool()
async def get_sitemaps(site_url: str) -> str:
    """
    List all sitemaps for a specific Search Console property.

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
    """
    try:
        service = get_gsc_service()
        sitemaps = service.sitemaps().list(siteUrl=site_url).execute()

        if not sitemaps.get("sitemap"):
            return f"No sitemaps found for {site_url}."

        result_lines = [f"Sitemaps for {site_url}:", "-" * 80,
                        "Path | Last Downloaded | Status | Indexed URLs | Errors", "-" * 80]

        for sitemap in sitemaps.get("sitemap", []):
            path = sitemap.get("path", "Unknown")
            last_downloaded = sitemap.get("lastDownloaded", "Never")
            if last_downloaded != "Never":
                try:
                    dt = datetime.fromisoformat(last_downloaded.replace("Z", "+00:00"))
                    last_downloaded = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

            status = "Has errors" if int(sitemap.get("errors", 0)) > 0 else "Valid"
            errors = int(sitemap.get("errors", 0))

            indexed_urls = "N/A"
            if "contents" in sitemap:
                for content in sitemap["contents"]:
                    if content.get("type") == "web":
                        indexed_urls = content.get("submitted", "0")
                        break

            result_lines.append(f"{path} | {last_downloaded} | {status} | {indexed_urls} | {errors}")

        return "\n".join(result_lines)
    except Exception as e:
        if "404" in str(e):
            return _site_not_found_error(site_url)
        return f"Error retrieving sitemaps: {str(e)}"


@mcp.tool()
async def inspect_url_enhanced(site_url: str, page_url: str) -> str:
    """
    Enhanced URL inspection to check indexing status and rich results in Google.

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
        page_url: The specific URL to inspect
    """
    try:
        service = get_gsc_service()
        request = {"inspectionUrl": page_url, "siteUrl": site_url}
        response = service.urlInspection().index().inspect(body=request).execute()

        if not response or "inspectionResult" not in response:
            return f"No inspection data found for {page_url}."

        inspection = response["inspectionResult"]
        result_lines = [f"URL Inspection for {page_url}:", "-" * 80]

        if "inspectionResultLink" in inspection:
            result_lines.append(f"Search Console Link: {inspection['inspectionResultLink']}")
            result_lines.append("-" * 80)

        index_status = inspection.get("indexStatusResult", {})
        verdict = index_status.get("verdict", "UNKNOWN")
        result_lines.append(f"Indexing Status: {verdict}")

        if "coverageState" in index_status:
            result_lines.append(f"Coverage: {index_status['coverageState']}")

        if "lastCrawlTime" in index_status:
            try:
                crawl_time = datetime.fromisoformat(index_status["lastCrawlTime"].replace("Z", "+00:00"))
                result_lines.append(f"Last Crawled: {crawl_time.strftime('%Y-%m-%d %H:%M')}")
            except Exception:
                result_lines.append(f"Last Crawled: {index_status['lastCrawlTime']}")

        for field, label in [
            ("pageFetchState", "Page Fetch"),
            ("robotsTxtState", "Robots.txt"),
            ("indexingState", "Indexing State"),
            ("googleCanonical", "Google Canonical"),
            ("crawledAs", "Crawled As"),
        ]:
            if field in index_status:
                result_lines.append(f"{label}: {index_status[field]}")

        if "userCanonical" in index_status and index_status.get("userCanonical") != index_status.get("googleCanonical"):
            result_lines.append(f"User Canonical: {index_status['userCanonical']}")

        if "referringUrls" in index_status and index_status["referringUrls"]:
            result_lines.append("\nReferring URLs:")
            for url in index_status["referringUrls"][:5]:
                result_lines.append(f"- {url}")
            if len(index_status["referringUrls"]) > 5:
                result_lines.append(f"... and {len(index_status['referringUrls']) - 5} more")

        if "richResultsResult" in inspection:
            rich = inspection["richResultsResult"]
            result_lines.append(f"\nRich Results: {rich.get('verdict', 'UNKNOWN')}")
            if "detectedItems" in rich and rich["detectedItems"]:
                result_lines.append("Detected Rich Result Types:")
                for item in rich["detectedItems"]:
                    result_lines.append(f"- {item.get('richResultType', 'Unknown')}")
                    if "items" in item and item["items"]:
                        for subitem in item["items"][:3]:
                            if "name" in subitem:
                                result_lines.append(f"  • {subitem['name']}")
                        if len(item["items"]) > 3:
                            result_lines.append(f"  • ... and {len(item['items']) - 3} more items")

        return "\n".join(result_lines)
    except Exception as e:
        if "404" in str(e):
            return _site_not_found_error(site_url)
        return f"Error inspecting URL: {str(e)}"


@mcp.tool()
async def batch_url_inspection(site_url: str, urls: str) -> str:
    """
    Inspect multiple URLs in batch (within API limits).

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
        urls: List of URLs to inspect, one per line
    """
    try:
        service = get_gsc_service()
        url_list = [url.strip() for url in urls.split("\n") if url.strip()]

        if not url_list:
            return "No URLs provided for inspection."
        if len(url_list) > 10:
            return f"Too many URLs provided ({len(url_list)}). Please limit to 10 URLs per batch to avoid API quota issues."

        results = []
        for page_url in url_list:
            try:
                response = service.urlInspection().index().inspect(
                    body={"inspectionUrl": page_url, "siteUrl": site_url}
                ).execute()

                if not response or "inspectionResult" not in response:
                    results.append(f"{page_url}: No inspection data found")
                    continue

                inspection = response["inspectionResult"]
                index_status = inspection.get("indexStatusResult", {})
                verdict = index_status.get("verdict", "UNKNOWN")
                coverage = index_status.get("coverageState", "Unknown")

                last_crawl = "Never"
                if "lastCrawlTime" in index_status:
                    try:
                        crawl_time = datetime.fromisoformat(index_status["lastCrawlTime"].replace("Z", "+00:00"))
                        last_crawl = crawl_time.strftime("%Y-%m-%d")
                    except Exception:
                        last_crawl = index_status["lastCrawlTime"]

                rich_results = "None"
                if "richResultsResult" in inspection:
                    rich = inspection["richResultsResult"]
                    if rich.get("verdict") == "PASS" and rich.get("detectedItems"):
                        rich_results = ", ".join(
                            item.get("richResultType", "Unknown") for item in rich["detectedItems"]
                        )

                results.append(
                    f"{page_url}:\n  Status: {verdict} - {coverage}\n  Last Crawl: {last_crawl}\n  Rich Results: {rich_results}\n"
                )
            except Exception as e:
                results.append(f"{page_url}: Error - {str(e)}")

        return f"Batch URL Inspection Results for {site_url}:\n\n" + "\n".join(results)
    except Exception as e:
        return f"Error performing batch inspection: {str(e)}"


@mcp.tool()
async def check_indexing_issues(site_url: str, urls: str) -> str:
    """
    Check for specific indexing issues across multiple URLs.

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
        urls: List of URLs to check, one per line
    """
    try:
        service = get_gsc_service()
        url_list = [url.strip() for url in urls.split("\n") if url.strip()]

        if not url_list:
            return "No URLs provided for inspection."
        if len(url_list) > 10:
            return f"Too many URLs provided ({len(url_list)}). Please limit to 10 URLs per batch to avoid API quota issues."

        issues_summary = {
            "not_indexed": [], "canonical_issues": [],
            "robots_blocked": [], "fetch_issues": [], "indexed": [],
        }

        for page_url in url_list:
            try:
                response = service.urlInspection().index().inspect(
                    body={"inspectionUrl": page_url, "siteUrl": site_url}
                ).execute()

                if not response or "inspectionResult" not in response:
                    issues_summary["not_indexed"].append(f"{page_url} - No inspection data found")
                    continue

                inspection = response["inspectionResult"]
                index_status = inspection.get("indexStatusResult", {})
                verdict = index_status.get("verdict", "UNKNOWN")
                coverage = index_status.get("coverageState", "Unknown")

                if verdict != "PASS" or "not indexed" in coverage.lower() or "excluded" in coverage.lower():
                    issues_summary["not_indexed"].append(f"{page_url} - {coverage}")
                else:
                    issues_summary["indexed"].append(page_url)

                google_canonical = index_status.get("googleCanonical", "")
                user_canonical = index_status.get("userCanonical", "")
                if google_canonical and user_canonical and google_canonical != user_canonical:
                    issues_summary["canonical_issues"].append(
                        f"{page_url} - Google chose: {google_canonical} instead of user-declared: {user_canonical}"
                    )

                if index_status.get("robotsTxtState") == "BLOCKED":
                    issues_summary["robots_blocked"].append(page_url)

                fetch_state = index_status.get("pageFetchState", "")
                if fetch_state != "SUCCESSFUL":
                    issues_summary["fetch_issues"].append(f"{page_url} - {fetch_state}")
            except Exception as e:
                issues_summary["not_indexed"].append(f"{page_url} - Error: {str(e)}")

        result_lines = [f"Indexing Issues Report for {site_url}:", "-" * 80,
                        f"Total URLs checked: {len(url_list)}",
                        f"Indexed: {len(issues_summary['indexed'])}",
                        f"Not indexed: {len(issues_summary['not_indexed'])}",
                        f"Canonical issues: {len(issues_summary['canonical_issues'])}",
                        f"Robots.txt blocked: {len(issues_summary['robots_blocked'])}",
                        f"Fetch issues: {len(issues_summary['fetch_issues'])}", "-" * 80]

        if issues_summary["not_indexed"]:
            result_lines.append("\nNot Indexed URLs:")
            result_lines.extend(f"- {i}" for i in issues_summary["not_indexed"])
        if issues_summary["canonical_issues"]:
            result_lines.append("\nCanonical Issues:")
            result_lines.extend(f"- {i}" for i in issues_summary["canonical_issues"])
        if issues_summary["robots_blocked"]:
            result_lines.append("\nRobots.txt Blocked URLs:")
            result_lines.extend(f"- {u}" for u in issues_summary["robots_blocked"])
        if issues_summary["fetch_issues"]:
            result_lines.append("\nFetch Issues:")
            result_lines.extend(f"- {i}" for i in issues_summary["fetch_issues"])

        return "\n".join(result_lines)
    except Exception as e:
        return f"Error checking indexing issues: {str(e)}"


@mcp.tool()
async def get_performance_overview(site_url: str, days: int = 28) -> str:
    """
    Get a performance overview for a specific property.

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
        days: Number of days to look back (default: 28)
    """
    try:
        service = get_gsc_service()
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)

        base_req = {
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate": end_date.strftime("%Y-%m-%d"),
            "dataState": DATA_STATE,
        }

        total_response = service.searchanalytics().query(
            siteUrl=site_url, body={**base_req, "dimensions": [], "rowLimit": 1}
        ).execute()

        date_response = service.searchanalytics().query(
            siteUrl=site_url, body={**base_req, "dimensions": ["date"], "rowLimit": days}
        ).execute()

        result_lines = [f"Performance Overview for {site_url} (last {days} days):", "-" * 80]

        if total_response.get("rows"):
            row = total_response["rows"][0]
            result_lines.append(f"Total Clicks: {row.get('clicks', 0):,}")
            result_lines.append(f"Total Impressions: {row.get('impressions', 0):,}")
            result_lines.append(f"Average CTR: {row.get('ctr', 0) * 100:.2f}%")
            result_lines.append(f"Average Position: {row.get('position', 0):.1f}")
        else:
            result_lines.append("No data available for the selected period.")
            return "\n".join(result_lines)

        if date_response.get("rows"):
            result_lines.append("\nDaily Trend:")
            result_lines.append("Date | Clicks | Impressions | CTR | Position")
            result_lines.append("-" * 80)
            for row in sorted(date_response["rows"], key=lambda x: x["keys"][0]):
                date_str = row["keys"][0]
                try:
                    date_formatted = datetime.strptime(date_str, "%Y-%m-%d").strftime("%m/%d")
                except Exception:
                    date_formatted = date_str
                result_lines.append(
                    f"{date_formatted} | {row.get('clicks', 0):.0f} | "
                    f"{row.get('impressions', 0):.0f} | "
                    f"{row.get('ctr', 0) * 100:.2f}% | "
                    f"{row.get('position', 0):.1f}"
                )

        return "\n".join(result_lines)
    except Exception as e:
        if "404" in str(e):
            return _site_not_found_error(site_url)
        return f"Error retrieving performance overview: {str(e)}"


@mcp.tool()
async def get_advanced_search_analytics(
    site_url: str,
    start_date: str = None,
    end_date: str = None,
    dimensions: str = "query",
    search_type: str = "WEB",
    row_limit: int = 1000,
    start_row: int = 0,
    sort_by: str = "clicks",
    sort_direction: str = "descending",
    filter_dimension: str = None,
    filter_operator: str = "contains",
    filter_expression: str = None,
    filters: str = None,
    data_state: str = None,
) -> str:
    """
    Get advanced search analytics data with sorting, filtering, and pagination.

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
        start_date: Start date in YYYY-MM-DD format (defaults to 28 days ago)
        end_date: End date in YYYY-MM-DD format (defaults to today)
        dimensions: Dimensions to group by, comma-separated (e.g., "query,page,device")
        search_type: Type of search results (WEB, IMAGE, VIDEO, NEWS, DISCOVER)
        row_limit: Maximum number of rows to return (max 25000)
        start_row: Starting row for pagination
        sort_by: Metric to sort by (clicks, impressions, ctr, position)
        sort_direction: Sort direction (ascending or descending)
        filter_dimension: Single filter dimension (query, page, country, device). Use 'filters' instead for multiple filters.
        filter_operator: Single filter operator (contains, equals, notContains, notEquals)
        filter_expression: Single filter expression value
        filters: JSON array of filter objects for AND logic across multiple dimensions. Overrides
                 filter_dimension/filter_operator/filter_expression when provided. Each object must
                 have 'dimension', 'operator', and 'expression' keys. Valid dimensions: query, page,
                 country, device. Valid operators: contains, equals, notContains, notEquals.
                 Example: [{"dimension":"country","operator":"equals","expression":"usa"},
                           {"dimension":"device","operator":"equals","expression":"MOBILE"}]
        data_state: Data freshness — "all" (default, matches GSC dashboard) or "final" (confirmed data only, 2-3 day lag)
    """
    try:
        service = get_gsc_service()

        if not end_date:
            end_date = datetime.now().date().strftime("%Y-%m-%d")
        if not start_date:
            start_date = (datetime.now().date() - timedelta(days=28)).strftime("%Y-%m-%d")

        resolved_data_state = (data_state or DATA_STATE).lower().strip()
        if resolved_data_state not in ("all", "final"):
            return (
                f"Invalid data_state value '{data_state}'. "
                "Accepted values are 'all' (matches GSC dashboard) or 'final' (2-3 day lag)."
            )

        dimension_list = [d.strip() for d in dimensions.split(",")]

        request = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": dimension_list,
            "rowLimit": min(row_limit, 25000),
            "startRow": start_row,
            "searchType": search_type.upper(),
            "dataState": resolved_data_state,
        }

        metric_map = {
            "clicks": "CLICK_COUNT",
            "impressions": "IMPRESSION_COUNT",
            "ctr": "CTR",
            "position": "POSITION",
        }
        if sort_by in metric_map:
            request["orderBy"] = [{"metric": metric_map[sort_by], "direction": sort_direction.lower()}]

        active_filters = []
        if filters:
            try:
                filter_list = json.loads(filters)
            except json.JSONDecodeError:
                return "Invalid filters JSON. Please provide a valid JSON array of filter objects."
            if not isinstance(filter_list, list) or len(filter_list) == 0:
                return "Invalid filters value. Expected a non-empty JSON array of filter objects."
            for f in filter_list:
                if not all(k in f for k in ("dimension", "operator", "expression")):
                    return (
                        "Each filter object must have 'dimension', 'operator', and 'expression' keys. "
                        f"Invalid filter: {f}"
                    )
            request["dimensionFilterGroups"] = [{"filters": filter_list}]
            active_filters = filter_list
        elif filter_dimension and filter_expression:
            single_filter = {
                "dimension": filter_dimension,
                "operator": filter_operator,
                "expression": filter_expression,
            }
            request["dimensionFilterGroups"] = [{"filters": [single_filter]}]
            active_filters = [single_filter]

        response = service.searchanalytics().query(siteUrl=site_url, body=request).execute()

        if not response.get("rows"):
            msg = (
                f"No search analytics data found for {site_url} with the specified parameters.\n\n"
                f"Parameters used:\n"
                f"- Date range: {start_date} to {end_date}\n"
                f"- Dimensions: {dimensions}\n"
                f"- Search type: {search_type}\n"
            )
            if active_filters:
                msg += "- Filters:\n"
                for f in active_filters:
                    msg += f"    {f['dimension']} {f['operator']} '{f['expression']}'\n"
            else:
                msg += "- No filter applied\n"
            return msg

        result_lines = [
            f"Search analytics for {site_url}:",
            f"Date range: {start_date} to {end_date}",
            f"Search type: {search_type}",
        ]
        if active_filters:
            result_lines.append(
                "Filters: " + " AND ".join(
                    f"{f['dimension']} {f['operator']} '{f['expression']}'" for f in active_filters
                )
            )
        result_lines.append(
            f"Showing rows {start_row + 1} to {start_row + len(response.get('rows', []))} "
            f"(sorted by {sort_by} {sort_direction})"
        )
        result_lines.append("\n" + "-" * 80 + "\n")

        header = [dim.capitalize() for dim in dimension_list]
        header.extend(["Clicks", "Impressions", "CTR", "Position"])
        result_lines.append(" | ".join(header))
        result_lines.append("-" * 80)

        for row in response.get("rows", []):
            data = [dim_value[:100] for dim_value in row.get("keys", [])]
            data.append(str(row.get("clicks", 0)))
            data.append(str(row.get("impressions", 0)))
            data.append(f"{row.get('ctr', 0) * 100:.2f}%")
            data.append(f"{row.get('position', 0):.1f}")
            result_lines.append(" | ".join(data))

        if len(response.get("rows", [])) == row_limit:
            next_start = start_row + row_limit
            result_lines.append("\nThere may be more results available. To see the next page, use:")
            result_lines.append(f"start_row: {next_start}, row_limit: {row_limit}")

        return "\n".join(result_lines)
    except Exception as e:
        if "404" in str(e):
            return _site_not_found_error(site_url)
        return f"Error retrieving advanced search analytics: {str(e)}"


@mcp.tool()
async def compare_search_periods(
    site_url: str,
    period1_start: str,
    period1_end: str,
    period2_start: str,
    period2_end: str,
    dimensions: str = "query",
    limit: int = 10,
) -> str:
    """
    Compare search analytics data between two time periods.

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
        period1_start: Start date for period 1 (YYYY-MM-DD)
        period1_end: End date for period 1 (YYYY-MM-DD)
        period2_start: Start date for period 2 (YYYY-MM-DD)
        period2_end: End date for period 2 (YYYY-MM-DD)
        dimensions: Dimensions to group by (default: query)
        limit: Number of top results to compare (default: 10)
    """
    try:
        service = get_gsc_service()
        dimension_list = [d.strip() for d in dimensions.split(",")]

        def _query(start, end):
            return service.searchanalytics().query(
                siteUrl=site_url,
                body={
                    "startDate": start, "endDate": end,
                    "dimensions": dimension_list, "rowLimit": 1000,
                    "dataState": DATA_STATE,
                },
            ).execute()

        p1_rows = _query(period1_start, period1_end).get("rows", [])
        p2_rows = _query(period2_start, period2_end).get("rows", [])

        if not p1_rows and not p2_rows:
            return f"No data found for either period for {site_url}."

        p1_data = {tuple(row.get("keys", [])): row for row in p1_rows}
        p2_data = {tuple(row.get("keys", [])): row for row in p2_rows}
        all_keys = set(p1_data.keys()) | set(p2_data.keys())

        comparison_data = []
        for key in all_keys:
            p1 = p1_data.get(key, {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0})
            p2 = p2_data.get(key, {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0})

            click_diff = p2.get("clicks", 0) - p1.get("clicks", 0)
            click_pct = (click_diff / p1.get("clicks", 1)) * 100 if p1.get("clicks", 0) > 0 else float("inf")
            pos_diff = p1.get("position", 0) - p2.get("position", 0)

            comparison_data.append({
                "key": key,
                "p1_clicks": p1.get("clicks", 0), "p2_clicks": p2.get("clicks", 0),
                "click_diff": click_diff, "click_pct": click_pct,
                "p1_position": p1.get("position", 0), "p2_position": p2.get("position", 0),
                "pos_diff": pos_diff,
            })

        comparison_data.sort(key=lambda x: abs(x["click_diff"]), reverse=True)

        result_lines = [
            f"Search analytics comparison for {site_url}:",
            f"Period 1: {period1_start} to {period1_end}",
            f"Period 2: {period2_start} to {period2_end}",
            f"Dimension(s): {dimensions}",
            f"Top {min(limit, len(comparison_data))} results by change in clicks:",
            "\n" + "-" * 100 + "\n",
            f"{' | '.join(d.capitalize() for d in dimension_list)} | P1 Clicks | P2 Clicks | Change | % | P1 Pos | P2 Pos | Pos Δ",
            "-" * 100,
        ]

        for item in comparison_data[:limit]:
            key_str = " | ".join(str(k)[:100] for k in item["key"])
            click_pct = item["click_pct"]
            click_pct_str = f"{click_pct:.1f}%" if click_pct != float("inf") else "N/A"
            result_lines.append(
                f"{key_str} | {item['p1_clicks']} | {item['p2_clicks']} | "
                f"{item['click_diff']:+d} | {click_pct_str} | "
                f"{item['p1_position']:.1f} | {item['p2_position']:.1f} | {item['pos_diff']:+.1f}"
            )

        return "\n".join(result_lines)
    except Exception as e:
        if "404" in str(e):
            return _site_not_found_error(site_url)
        return f"Error comparing search periods: {str(e)}"


@mcp.tool()
async def get_search_by_page_query(
    site_url: str,
    page_url: str,
    days: int = 28,
    row_limit: int = 20,
) -> str:
    """
    Get search analytics data for a specific page, broken down by query.

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
        page_url: The specific page URL to analyze
        days: Number of days to look back (default: 28)
        row_limit: Number of rows to return (default: 20, max: 500).
    """
    try:
        service = get_gsc_service()
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)

        request = {
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate": end_date.strftime("%Y-%m-%d"),
            "dimensions": ["query"],
            "dimensionFilterGroups": [{"filters": [{"dimension": "page", "operator": "equals", "expression": page_url}]}],
            "rowLimit": min(max(1, row_limit), 500),
            "orderBy": [{"metric": "CLICK_COUNT", "direction": "descending"}],
            "dataState": DATA_STATE,
        }

        response = service.searchanalytics().query(siteUrl=site_url, body=request).execute()

        if not response.get("rows"):
            return f"No search data found for page {page_url} in the last {days} days."

        result_lines = [f"Search queries for page {page_url} (last {days} days):",
                        "\n" + "-" * 80 + "\n",
                        "Query | Clicks | Impressions | CTR | Position", "-" * 80]

        total_clicks = total_impressions = 0
        for row in response.get("rows", []):
            query = row.get("keys", ["Unknown"])[0]
            clicks = row.get("clicks", 0)
            impressions = row.get("impressions", 0)
            total_clicks += clicks
            total_impressions += impressions
            result_lines.append(
                f"{query[:100]} | {clicks} | {impressions} | "
                f"{row.get('ctr', 0) * 100:.2f}% | {row.get('position', 0):.1f}"
            )

        avg_ctr = (total_clicks / total_impressions * 100) if total_impressions > 0 else 0
        result_lines.append("-" * 80)
        result_lines.append(f"TOTAL | {total_clicks} | {total_impressions} | {avg_ctr:.2f}% | -")

        return "\n".join(result_lines)
    except Exception as e:
        return f"Error retrieving page query data: {str(e)}"


@mcp.tool()
async def list_sitemaps_enhanced(site_url: str, sitemap_index: str = None) -> str:
    """
    List all sitemaps for a specific Search Console property with detailed information.

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
        sitemap_index: Optional sitemap index URL to list child sitemaps
    """
    try:
        service = get_gsc_service()

        if sitemap_index:
            sitemaps = service.sitemaps().list(siteUrl=site_url, sitemapIndex=sitemap_index).execute()
            source = f"child sitemaps from index: {sitemap_index}"
        else:
            sitemaps = service.sitemaps().list(siteUrl=site_url).execute()
            source = "all submitted sitemaps"

        if not sitemaps.get("sitemap"):
            return f"No sitemaps found for {site_url}" + (f" in index {sitemap_index}" if sitemap_index else ".")

        result_lines = [f"Sitemaps for {site_url} ({source}):", "-" * 100,
                        "Path | Last Submitted | Last Downloaded | Type | URLs | Errors | Warnings",
                        "-" * 100]

        for sitemap in sitemaps.get("sitemap", []):
            path = sitemap.get("path", "Unknown")

            def fmt_date(val):
                if not val or val == "Never":
                    return "Never"
                try:
                    return datetime.fromisoformat(val.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    return val

            last_submitted = fmt_date(sitemap.get("lastSubmitted", "Never"))
            last_downloaded = fmt_date(sitemap.get("lastDownloaded", "Never"))
            sitemap_type = "Index" if sitemap.get("isSitemapsIndex", False) else "Sitemap"
            errors = int(sitemap.get("errors", 0))
            warnings = int(sitemap.get("warnings", 0))

            url_count = "N/A"
            for content in sitemap.get("contents", []):
                if content.get("type") == "web":
                    url_count = content.get("submitted", "0")
                    break

            result_lines.append(f"{path} | {last_submitted} | {last_downloaded} | {sitemap_type} | {url_count} | {errors} | {warnings}")

        pending_count = sum(1 for s in sitemaps.get("sitemap", []) if s.get("isPending", False))
        if pending_count > 0:
            result_lines.append(f"\nNote: {pending_count} sitemaps are still pending processing by Google.")

        return "\n".join(result_lines)
    except Exception as e:
        if "404" in str(e):
            return _site_not_found_error(site_url)
        return f"Error retrieving sitemaps: {str(e)}"


@mcp.tool()
async def get_sitemap_details(site_url: str, sitemap_url: str) -> str:
    """
    Get detailed information about a specific sitemap.

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
        sitemap_url: The full URL of the sitemap to inspect
    """
    try:
        service = get_gsc_service()
        details = service.sitemaps().get(siteUrl=site_url, feedpath=sitemap_url).execute()

        if not details:
            return f"No details found for sitemap {sitemap_url}."

        result_lines = [f"Sitemap Details for {sitemap_url}:", "-" * 80]
        is_index = details.get("isSitemapsIndex", False)
        result_lines.append(f"Type: {'Sitemap Index' if is_index else 'Sitemap'}")
        result_lines.append(f"Status: {'Pending processing' if details.get('isPending', False) else 'Processed'}")

        for field, label in [("lastSubmitted", "Last Submitted"), ("lastDownloaded", "Last Downloaded")]:
            if field in details:
                try:
                    dt = datetime.fromisoformat(details[field].replace("Z", "+00:00"))
                    result_lines.append(f"{label}: {dt.strftime('%Y-%m-%d %H:%M')}")
                except Exception:
                    result_lines.append(f"{label}: {details[field]}")

        result_lines.append(f"Errors: {details.get('errors', 0)}")
        result_lines.append(f"Warnings: {details.get('warnings', 0)}")

        if details.get("contents"):
            result_lines.append("\nContent Breakdown:")
            for content in details["contents"]:
                result_lines.append(
                    f"- {content.get('type', 'Unknown').upper()}: "
                    f"{content.get('submitted', 0)} submitted, {content.get('indexed', 'N/A')} indexed"
                )

        if is_index:
            result_lines.append(f"\nThis is a sitemap index. To list child sitemaps, use:")
            result_lines.append(f"list_sitemaps_enhanced with sitemap_index={sitemap_url}")

        return "\n".join(result_lines)
    except Exception as e:
        return f"Error retrieving sitemap details: {str(e)}"


@mcp.tool()
async def submit_sitemap(site_url: str, sitemap_url: str) -> str:
    """
    Submit a new sitemap or resubmit an existing one to Google.

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
        sitemap_url: The full URL of the sitemap to submit
    """
    try:
        service = get_gsc_service()
        service.sitemaps().submit(siteUrl=site_url, feedpath=sitemap_url).execute()

        try:
            details = service.sitemaps().get(siteUrl=site_url, feedpath=sitemap_url).execute()
            result_lines = [f"Successfully submitted sitemap: {sitemap_url}"]
            if "lastSubmitted" in details:
                try:
                    dt = datetime.fromisoformat(details["lastSubmitted"].replace("Z", "+00:00"))
                    result_lines.append(f"Submission time: {dt.strftime('%Y-%m-%d %H:%M')}")
                except Exception:
                    result_lines.append(f"Submission time: {details['lastSubmitted']}")
            result_lines.append(
                f"Status: {'Pending processing' if details.get('isPending', True) else 'Processing started'}"
            )
            result_lines.append("\nNote: Google may take some time to process the sitemap. Check back later for full details.")
            return "\n".join(result_lines)
        except Exception:
            return f"Successfully submitted sitemap: {sitemap_url}\n\nGoogle will queue it for processing."
    except Exception as e:
        return f"Error submitting sitemap: {str(e)}"


@mcp.tool()
async def manage_sitemaps(site_url: str, action: str, sitemap_url: str = None, sitemap_index: str = None) -> str:
    """
    All-in-one tool to manage sitemaps (list, get details, submit).

    Args:
        site_url: Exact GSC property URL from list_properties (e.g. "https://example.com/" or
                  "sc-domain:example.com"). Domain properties cover all subdomains — use the
                  domain property as site_url and filter by page to analyze a specific subdomain.
        action: The action to perform (list, details, submit)
        sitemap_url: The full URL of the sitemap (required for details, submit)
        sitemap_index: Optional sitemap index URL for listing child sitemaps (only used with 'list' action)
    """
    try:
        action = action.lower().strip()
        valid_actions = ["list", "details", "submit"]

        if action not in valid_actions:
            return f"Invalid action: {action}. Please use one of: {', '.join(valid_actions)}"

        if action in ["details", "submit"] and not sitemap_url:
            return f"The {action} action requires a sitemap_url parameter."

        if action == "list":
            return await list_sitemaps_enhanced(site_url, sitemap_index)
        elif action == "details":
            return await get_sitemap_details(site_url, sitemap_url)
        elif action == "submit":
            return await submit_sitemap(site_url, sitemap_url)
    except Exception as e:
        return f"Error managing sitemaps: {str(e)}"


@mcp.tool()
async def get_creator_info() -> str:
    """
    Provides information about Amin Foroutan, the creator of the MCP-GSC tool.
    """
    return """
# About the Creator: Amin Foroutan

Amin Foroutan is an SEO consultant with over a decade of experience, specializing in technical SEO, Python-driven tools, and data analysis for SEO performance.

## Connect with Amin:

- **LinkedIn**: [Amin Foroutan](https://www.linkedin.com/in/ma-foroutan/)
- **Personal Website**: [aminforoutan.com](https://aminforoutan.com/)
- **YouTube**: [Amin Forout](https://www.youtube.com/channel/UCW7tPXg-rWdH4YzLrcAdBIw)
- **X (Twitter)**: [@aminfseo](https://x.com/aminfseo)

## Notable Projects:

Amin has created several popular SEO tools including:
- Advanced GSC Visualizer (6.4K+ users)
- SEO Render Insight Tool (3.5K+ users)
- Google AI Overview Impact Analysis (1.2K+ users)
- Google AI Overview Citation Analysis (900+ users)
- SEMRush Enhancer (570+ users)
- SEO Page Inspector (115+ users)
"""


@mcp.tool()
async def reauthenticate() -> str:
    """
    Clear your current Google authorization and get a link to re-authorize.
    Use this if the wrong Google account was authorized, or you want to switch accounts.
    """
    if MCP_TRANSPORT == "stdio":
        # Local/stdio mode: delete token and run local OAuth flow
        try:
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
                token_deleted = True
            else:
                token_deleted = False

            if not OAUTH_CLIENT_ID or not OAUTH_CLIENT_SECRET:
                return (
                    "Error: GSC_OAUTH_CLIENT_ID and GSC_OAUTH_CLIENT_SECRET must be set in .env. "
                    "Cannot start new authentication flow."
                )

            flow = InstalledAppFlow.from_client_config(_build_installed_client_config(), SCOPES)
            creds = flow.run_local_server(port=8080)
            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())

            msg = "Successfully authenticated with a new Google account."
            if token_deleted:
                msg = "Previous session deleted. " + msg
            return msg
        except Exception as e:
            return f"Error during reauthentication: {str(e)}"
    else:
        # SSE/multi-tenant mode: delete the user's token and return a setup URL
        user_key = current_user_key.get()
        if not user_key:
            return "Error: No API key found in this connection."

        token_file = _token_file(user_key)
        if os.path.exists(token_file):
            os.remove(token_file)

        return (
            "Your Google authorization has been cleared.\n\n"
            "To re-authorize with the correct account:\n\n"
            f"  1. Switch to the correct Google account in your browser\n"
            f"  2. Visit: {SERVER_URL}/setup?key={user_key}\n\n"
            "After completing authorization, come back here and try list_properties to confirm."
        )


# ─── Web endpoints ─────────────────────────────────────────────────────────────

_SETUP_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>GSC MCP — Connect Your Google Account</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 640px; margin: 60px auto; padding: 0 24px; color: #222; }}
    h1 {{ font-size: 1.6rem; margin-bottom: 0.25rem; }}
    .sub {{ color: #666; margin-bottom: 2rem; }}
    .btn {{ display: inline-block; background: #4285F4; color: #fff; padding: 12px 28px;
            border-radius: 6px; text-decoration: none; font-weight: 600; margin-top: 1rem; }}
    .btn:hover {{ background: #3367D6; }}
    .warning {{ background: #fff8e1; border-left: 4px solid #f9a825; padding: 12px 16px;
                border-radius: 4px; margin: 1.5rem 0; font-size: 0.92rem; }}
    .key {{ font-family: monospace; background: #f5f5f5; padding: 2px 6px; border-radius: 3px; }}
  </style>
</head>
<body>
  <h1>Connect Your Google Search Console</h1>
  <p class="sub">This will authorize read-only access to your GSC properties.</p>

  <div class="warning">
    <strong>Before clicking below:</strong> Make sure you are signed into the <em>correct</em>
    Google account in this browser. The account that approves the next screen is the one
    that will be authorized.
  </div>

  <p>Your API key: <span class="key">{user_key}</span></p>
  <p>Save this key — you will need it to configure Claude Desktop.</p>

  <a class="btn" href="{auth_url}">Authorize with Google &rarr;</a>
</body>
</html>"""

_SUCCESS_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>GSC MCP — Authorization Complete</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 760px; margin: 60px auto; padding: 0 24px; color: #222; line-height: 1.6; }}
    h1 {{ color: #2e7d32; margin-bottom: 0.25rem; }}
    h2 {{ margin-top: 2rem; border-bottom: 1px solid #e0e0e0; padding-bottom: 4px; }}
    pre {{ background: #f5f5f5; padding: 16px; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; white-space: pre-wrap; word-break: break-all; }}
    .key-box {{ font-family: monospace; background: #e8f5e9; border: 1px solid #a5d6a7;
                padding: 12px 16px; border-radius: 6px; font-size: 1.05rem; display: block;
                margin: 1rem 0; word-break: break-all; }}
    .warn {{ background: #fff3e0; border-left: 4px solid #ff9800; padding: 12px 16px; border-radius: 4px; margin: 1rem 0; }}
    .info {{ background: #e3f2fd; border-left: 4px solid #1976d2; padding: 12px 16px; border-radius: 4px; margin: 1rem 0; }}
    .step {{ background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 16px; margin: 12px 0; }}
    .step-num {{ display: inline-block; background: #1976d2; color: #fff; border-radius: 50%;
                 width: 24px; height: 24px; text-align: center; line-height: 24px; font-size: 0.85rem;
                 font-weight: bold; margin-right: 8px; }}
    code {{ background: #f0f0f0; padding: 2px 5px; border-radius: 3px; font-size: 0.9em; }}
    .path {{ font-family: monospace; font-size: 0.88rem; color: #555; }}
  </style>
</head>
<body>
  <h1>&#10003; Authorization Successful!</h1>
  <p>Your Google account has been connected to the GSC MCP server.</p>

  <h2>Your API Key</h2>
  <p>This key identifies you on the server. Keep it safe — anyone with it can read your GSC data.</p>
  <span class="key-box">{user_key}</span>
  <div class="warn"><strong>Save this key now.</strong> If you lose it, visit <code>/setup</code> again to generate a new one.</div>

  <h2>Step 1 — Find your Claude Desktop config file</h2>

  <div class="step">
    <span class="step-num">A</span> <strong>Windows (standard installer)</strong><br>
    <span class="path">%APPDATA%\\Claude\\claude_desktop_config.json</span><br>
    <small>Usually: <code>C:\\Users\\YourName\\AppData\\Roaming\\Claude\\claude_desktop_config.json</code></small>
  </div>

  <div class="step">
    <span class="step-num">B</span> <strong>Windows (Microsoft Store version)</strong><br>
    <span class="path">%LOCALAPPDATA%\\Packages\\Claude_&lt;id&gt;\\LocalCache\\Roaming\\Claude\\claude_desktop_config.json</span>
  </div>

  <div class="step">
    <span class="step-num">C</span> <strong>macOS</strong><br>
    <span class="path">~/Library/Application Support/Claude/claude_desktop_config.json</span>
  </div>

  <h2>Step 2 — Add the GSC server to your config</h2>

  <p>Open the file above and merge the <code>"gsc"</code> entry into the <code>"mcpServers"</code> object.
  Choose the option that matches your Claude Desktop version.</p>

  <h3 style="margin-top:1.5rem">&#9654; Option A — Modern Claude Desktop (recommended)</h3>
  <p>Supported in Claude Desktop v0.7 and newer. Uses a direct URL connection.</p>
  <p><strong>Full config (paste if file is empty or missing):</strong></p>
  <pre>{full_config}</pre>
  <p><strong>Just the <code>"gsc"</code> block (add inside your existing <code>"mcpServers"</code>):</strong></p>
  <pre>{gsc_block}</pre>

  <h3 style="margin-top:1.5rem">&#9654; Option B — Legacy Claude Desktop</h3>
  <p>Use this if Option A gives a <em>"command is required"</em> error. Requires <strong>Node.js</strong> installed on your machine — <a href="https://nodejs.org" target="_blank">download here</a> if you don't have it.</p>
  <p><strong>Full config (paste if file is empty or missing):</strong></p>
  <pre>{legacy_full_config}</pre>
  <p><strong>Just the <code>"gsc"</code> block (add inside your existing <code>"mcpServers"</code>):</strong></p>
  <pre>{legacy_gsc_block}</pre>

  <div class="info">
    Option B uses <code>mcp-remote</code>, a small bridge that runs locally and forwards your
    requests to this server. Your API key is embedded in the URL so the server knows whose
    GSC account to use.
  </div>

  <h2>Step 3 — Restart Claude Desktop</h2>
  <ol>
    <li>Fully quit Claude Desktop — right-click the system tray icon → Quit,<br>
        <em>or</em> open Task Manager → find Claude.exe → End Task</li>
    <li>Reopen Claude Desktop</li>
    <li>Open a new chat and type: <code>list_properties</code></li>
    <li>You should see your GSC properties listed ✓</li>
  </ol>

  <h2>Wrong Google account?</h2>
  <p>Type <code>reauthenticate</code> in a Claude Desktop chat. The server will clear your token
  and give you a link to sign in with the correct account.</p>
</body>
</html>"""


async def handle_setup(request: Request) -> Response:
    """Initiate the OAuth flow for a user."""
    if not OAUTH_CLIENT_ID or not OAUTH_CLIENT_SECRET:
        logger.error("Setup requested but GSC_OAUTH_CLIENT_ID / GSC_OAUTH_CLIENT_SECRET not set in .env")
        return HTMLResponse(
            "<h1>Server not configured</h1>"
            "<p><code>GSC_OAUTH_CLIENT_ID</code> and <code>GSC_OAUTH_CLIENT_SECRET</code> "
            "must be set in the server's <code>.env</code> file.</p>",
            status_code=500,
        )

    user_key = request.query_params.get("key") or secrets.token_urlsafe(32)
    logger.info("OAuth setup initiated for key ...%s (ip=%s)", user_key[-6:], request.client.host if request.client else "unknown")

    flow = Flow.from_client_config(
        _build_web_client_config(),
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )

    # Persist state → user_key + code_verifier so callback can complete PKCE exchange
    states = _load_states()
    now = datetime.utcnow().timestamp()
    # Prune expired states while we're here
    states = {k: v for k, v in states.items() if now - v.get("ts", 0) < _STATE_TTL_SECONDS}
    states[state] = {
        "user_key": user_key,
        "ts": now,
        "code_verifier": getattr(flow, "code_verifier", None),
    }
    _save_states(states)

    return HTMLResponse(_SETUP_PAGE.format(user_key=user_key, auth_url=auth_url))


async def handle_oauth_callback(request: Request) -> Response:
    """Handle the OAuth redirect from Google."""
    error = request.query_params.get("error")
    if error:
        logger.warning("OAuth callback returned error: %s", error)
        return HTMLResponse(
            f"<h1>Authorization failed</h1><p>Google returned: <code>{error}</code></p>"
            f"<p><a href='/setup'>Try again</a></p>",
            status_code=400,
        )

    state = request.query_params.get("state")
    code = request.query_params.get("code")

    state_entry = _consume_oauth_state(state)
    if not state_entry:
        logger.warning("OAuth callback received invalid or expired state token")
        return HTMLResponse(
            "<h1>Invalid or expired session</h1>"
            "<p>The authorization link has expired (10 minute limit). "
            "<a href='/setup'>Please start again</a>.</p>",
            status_code=400,
        )

    user_key = state_entry["user_key"]
    code_verifier = state_entry.get("code_verifier")

    try:
        flow = Flow.from_client_config(
            _build_web_client_config(),
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
            state=state,
        )
        if code_verifier:
            flow.code_verifier = code_verifier
        flow.fetch_token(code=code)
        creds = flow.credentials

        token_file = _token_file(user_key)
        with open(token_file, "w") as f:
            f.write(creds.to_json())
        logger.info("OAuth complete — token saved for key ...%s", user_key[-6:])
    except Exception as e:
        logger.error("Token exchange failed for key ...%s: %s", user_key[-6:], e)
        return HTMLResponse(
            f"<h1>Token exchange failed</h1><p>{str(e)}</p>"
            f"<p><a href='/setup?key={user_key}'>Try again</a></p>",
            status_code=500,
        )

    sse_url = f"{SERVER_URL}/sse?key={user_key}"

    # Modern Claude Desktop config (url format)
    full_config = json.dumps({"mcpServers": {"gsc": {"url": sse_url}}}, indent=2)
    gsc_block = json.dumps({"gsc": {"url": sse_url}}, indent=2)

    # Legacy Claude Desktop config (mcp-remote bridge via npx)
    legacy_entry = {"command": "npx", "args": ["-y", "mcp-remote", sse_url]}
    legacy_full_config = json.dumps({"mcpServers": {"gsc": legacy_entry}}, indent=2)
    legacy_gsc_block = json.dumps({"gsc": legacy_entry}, indent=2)

    return HTMLResponse(_SUCCESS_PAGE.format(
        user_key=user_key,
        full_config=full_config,
        gsc_block=gsc_block,
        legacy_full_config=legacy_full_config,
        legacy_gsc_block=legacy_gsc_block,
    ))


async def handle_health(request: Request) -> Response:
    return JSONResponse({"status": "ok", "transport": MCP_TRANSPORT})


# ─── Middleware ────────────────────────────────────────────────────────────────

class UserKeyMiddleware(BaseHTTPMiddleware):
    """Extract ?key= from the SSE connection URL and set it in the context var."""

    async def dispatch(self, request: Request, call_next):
        key = request.query_params.get("key") or request.headers.get("x-api-key")
        path = request.url.path
        if key:
            sanitized = _sanitize_key(key)
            logger.info("Request  %s %s  key=...%s", request.method, path, sanitized[-6:])
            token = current_user_key.set(sanitized)
            try:
                response = await call_next(request)
            finally:
                current_user_key.reset(token)
        else:
            if path not in ("/health",):
                logger.info("Request  %s %s  (no key)", request.method, path)
            response = await call_next(request)
        return response


# ─── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if MCP_TRANSPORT == "stdio":
        # Local mode — original behaviour
        mcp.run(transport="stdio")
    else:
        # SSE/HTTP mode for VPS hosting
        sse_transport = SseServerTransport("/messages/")

        async def handle_sse(request: Request) -> Response:
            user_key = current_user_key.get() or "unknown"
            logger.info("SSE connection opened  key=...%s", user_key[-6:])
            try:
                async with sse_transport.connect_sse(
                    request.scope, request.receive, request._send
                ) as streams:
                    await mcp._mcp_server.run(
                        streams[0],
                        streams[1],
                        mcp._mcp_server.create_initialization_options(),
                    )
            finally:
                logger.info("SSE connection closed  key=...%s", user_key[-6:])
            return Response()

        app = Starlette(
            routes=[
                Route("/health", endpoint=handle_health),
                Route("/setup", endpoint=handle_setup),
                Route("/oauth/callback", endpoint=handle_oauth_callback),
                Route("/sse", endpoint=handle_sse),
                Mount("/messages/", app=sse_transport.handle_post_message),
            ]
        )
        app.add_middleware(UserKeyMiddleware)

        logger.info("=" * 60)
        logger.info("GSC MCP Server starting")
        logger.info("  Transport : SSE (HTTP)")
        logger.info("  Port      : %s", PORT)
        logger.info("  Public URL: %s", SERVER_URL)
        logger.info("  Setup page: %s/setup", SERVER_URL)
        logger.info("  Health    : %s/health", SERVER_URL)
        logger.info("  Data dir  : %s", DATA_DIR)
        logger.info("=" * 60)

        uvicorn.run(
            app,
            host="0.0.0.0",
            port=PORT,
            log_level="info",
            access_log=True,
        )
