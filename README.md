# Google Search Console MCP server for SEOs

> **March 2026 (v0.2.1):** Data freshness, flexible row limits, multi-dimension filtering, reauthenticate tool, bug fixes, and multi-client support. See the [Changelog](#changelog) for details.

A Model Context Protocol (MCP) server that connects [Google Search Console](https://search.google.com/search-console/about) (GSC) to AI assistants, allowing you to analyze your SEO data through natural language conversations. Works with **Claude**, **Cursor**, **Codex**, **Gemini CLI**, **Antigravity**, and any other MCP-compatible client. This integration gives you access to property information, search analytics, URL inspection, and sitemap management—all through simple chat.

---

## What Can This Tool Do For SEO Professionals?

1. **Property Management**  
   - See all your GSC properties in one place
   - Get verification details and basic site information
   - Add new properties to your account
   - Remove properties from your account

2. **Search Analytics & Reporting**  
   - Discover which search queries bring visitors to your site
   - Track impressions, clicks, and click-through rates
   - Analyze performance trends over time
   - Compare different time periods to spot changes
   - **Visualize your data** with charts and graphs created by Claude

3. **URL Inspection & Indexing**  
   - Check if specific pages have indexing problems
   - See when Google last crawled your pages
   - Inspect multiple URLs at once to identify patterns
   - Get actionable insights on how to improve indexing

4. **Sitemap Management**  
   - View all your sitemaps and their status
   - Submit new sitemaps directly through Claude
   - Check for errors or warnings in your sitemaps
   - Monitor sitemap processing status

---

## Available Tools

Here's what you can ask your AI assistant to do once you've set up this integration:

| **What You Can Ask For**        | **What It Does**                                            | **What You'll Need to Provide**                                 |
|---------------------------------|-------------------------------------------------------------|----------------------------------------------------------------|
| `list_properties`               | Shows all your GSC properties                               | Nothing - just ask!                                             |
| `get_site_details`              | Shows details about a specific site                         | Your website URL                                                |
| `add_site`                      | Adds a new site to your GSC properties                      | Your website URL                                                |
| `delete_site`                   | Removes a site from your GSC properties                     | Your website URL                                                |
| `get_search_analytics`          | Shows top queries and pages with metrics                    | Your website URL, time period, and optional `row_limit` (default 20, max 500) |
| `get_performance_overview`      | Gives a summary of site performance                         | Your website URL and time period                                |
| `check_indexing_issues`         | Checks if pages have indexing problems                      | Your website URL and list of pages to check                     |
| `inspect_url_enhanced`          | Detailed inspection of a specific URL                       | Your website URL and the page to inspect                        |
| `get_sitemaps`                  | Lists all sitemaps for your site                            | Your website URL                                                |
| `submit_sitemap`                | Submits a new sitemap to Google                             | Your website URL and sitemap URL                                |

*For a complete list of all 19 available tools and their detailed descriptions, ask your AI assistant to "list tools" after setup.*

---

## Getting Started

### Quick setup with Claude Code CLI (recommended for Windows)

If you have [Claude Code CLI](https://github.com/anthropics/claude-code) installed, you can automate the entire setup:

1. Open a terminal **inside the project folder**
2. Run `claude` to start Claude Code
3. Paste the contents of `codex_mcp_setup_prompt.txt` — Claude will walk you through every step interactively

---

### Manual setup

### 1. Install Required Software

You'll need:

- [Python](https://www.python.org/downloads/) (version 3.11 or newer)
- An MCP-compatible AI client — [Claude Desktop](https://claude.ai/download), [Cursor](https://www.cursor.com/), [Codex CLI](https://github.com/openai/codex), [Gemini CLI](https://github.com/google-gemini/gemini-cli), or [Antigravity](https://antigravity.ai/)

### 2. Download the project

Click the green "Code" button → "Download ZIP" and unzip to a folder you can find easily (e.g. Documents), or clone with Git:

```bash
git clone https://github.com/AminForou/mcp-gsc.git
```

### 3. Set up Google Cloud Console (A to Z)

You need to create OAuth credentials that allow your AI client to access your GSC data. Follow all parts in order.

#### Part A — Create or select a project

1. Go to [console.cloud.google.com](https://console.cloud.google.com/)
2. At the top click the project dropdown → **New Project** (or select an existing one)
3. Give it a name (e.g. "GSC MCP") → click **Create**
4. Make sure the project is selected in the top dropdown

#### Part B — Enable the Search Console API

5. Go to **APIs & Services → Library**
6. Search for **Google Search Console API**
7. Click it → click **Enable** (if it already says "Manage" it's already enabled)

#### Part C — Configure the OAuth consent screen

8. Go to **APIs & Services → OAuth consent screen**
9. User type: **External** → click **Create**
10. Fill in:
    - App name: anything (e.g. "GSC MCP")
    - User support email: your email
    - Developer contact email: your email
11. Click **Save and Continue**
12. On the Scopes screen click **Add or Remove Scopes**, search for and add:
    `https://www.googleapis.com/auth/webmasters.readonly`
    → click **Update** → **Save and Continue**
13. On the Test Users screen click **+ Add Users**, add the Google account email that has access to your GSC properties → **Save and Continue**
14. Review summary → click **Back to Dashboard**

> **Why test users?** While your app is in "testing" mode (not published), only accounts listed here can complete the OAuth flow. If you skip this step, you'll get an "access blocked" error.

#### Part D — Create the OAuth 2.0 client (Desktop App)

15. Go to **APIs & Services → Credentials**
16. Click **+ CREATE CREDENTIALS → OAuth 2.0 Client ID**
17. Application type: **Desktop app**
18. Name: anything (e.g. "GSC MCP Desktop") → click **Create**
19. In the dialog click **Download JSON**
20. Rename the file to `client_secrets.json`
21. Move it into the project folder (replace any existing file)

**🎬 Watch this beginner-friendly tutorial on Youtube:**

<div align="center">
  <a href="https://youtu.be/PCWsK5BgSd0">
    <img src="https://i.ytimg.com/vi/PCWsK5BgSd0/maxresdefault.jpg" alt="Google Search Console API Setup Tutorial" width="600" style="margin: 20px 0; border-radius: 8px;">
  </a>
</div>

*Click the image above to watch the step-by-step video tutorial*

#### Alternative: Service Account Authentication

Use this instead of OAuth if you need automated/headless access.

1. Go to **APIs & Services → Credentials**
2. Click **+ CREATE CREDENTIALS → Service Account**, fill in details → **Create**
3. Click the new service account → **Keys** tab → **Add Key → Create new key → JSON** → download
4. Save the file as `service_account_credentials.json` in the project folder
5. Add the service account email as a user in your Google Search Console properties

### 4. Install dependencies

Open a terminal in the project folder:

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

**Mac/Linux:**
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 5. Configure your AI client

The MCP server is a **stdio server** — your AI client launches it automatically. Do not run it manually in a terminal.

Open the Claude Desktop config file:

- **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

If the file doesn't exist, create it. Add the `gsc` entry to `mcpServers` (merge with any existing entries, don't replace them):

#### OAuth authentication (recommended)

**Windows paths:**
```json
{
  "mcpServers": {
    "gsc": {
      "command": "C:\\full\\path\\to\\mcp-gsc\\.venv\\Scripts\\python.exe",
      "args": ["C:\\full\\path\\to\\mcp-gsc\\gsc_server.py"],
      "env": {
        "GSC_DATA_STATE": "all"
      }
    }
  }
}
```

**Mac/Linux paths:**
```json
{
  "mcpServers": {
    "gsc": {
      "command": "/full/path/to/mcp-gsc/.venv/bin/python",
      "args": ["/full/path/to/mcp-gsc/gsc_server.py"],
      "env": {
        "GSC_DATA_STATE": "all"
      }
    }
  }
}
```

#### Service account authentication

Add `GSC_CREDENTIALS_PATH` and `GSC_SKIP_OAUTH` to the `env` block:

```json
"env": {
  "GSC_CREDENTIALS_PATH": "C:\\full\\path\\to\\service_account_credentials.json",
  "GSC_SKIP_OAUTH": "true",
  "GSC_DATA_STATE": "all"
}
```

#### Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `GSC_OAUTH_CLIENT_SECRETS_FILE` | OAuth only | `client_secrets.json` (same folder) | Path to your OAuth client secrets JSON file |
| `GSC_CREDENTIALS_PATH` | Service account only | `service_account_credentials.json` (same folder) | Path to your service account JSON key file |
| `GSC_SKIP_OAUTH` | No | `false` | Set to `"true"` to force service account auth and skip OAuth |
| `GSC_DATA_STATE` | No | `"all"` | `"all"` returns fresh data matching the GSC dashboard. `"final"` returns only confirmed data (2–3 day lag). |

### 6. Restart Claude Desktop and authorize

> **Important:** Before restarting Claude Desktop, open your browser and make sure you are signed into the Google account that has access to your GSC properties. Whichever account is active in the browser when Claude Desktop first starts is the one that gets authorized.

1. Fully quit Claude Desktop (system tray → Quit, or Task Manager → End Task on Windows)
2. Reopen Claude Desktop
3. A browser tab will open asking you to authorize Google Search Console — approve it with the correct account
4. In Claude Desktop, type `list_sites` to confirm everything is working

If the wrong Google account was authorized, use the `reauthenticate` tool inside Claude Desktop to redo the OAuth flow.

### 7. Start Analyzing Your SEO Data!

Now you can ask your AI assistant questions about your GSC data! It can not only retrieve the data but also analyze it, explain trends, and create visualizations to help you understand your SEO performance better.

Here are some powerful prompts you can use with each tool:

| **Tool Name**                   | **Sample Prompt**                                                                                |
|---------------------------------|--------------------------------------------------------------------------------------------------|
| `list_properties`               | "List all my GSC properties and tell me which ones have the most pages indexed."                 |
| `get_site_details`              | "Analyze the verification status of mywebsite.com and explain what the ownership details mean."  |
| `add_site`                      | "Add my new website https://mywebsite.com to Search Console and verify its status."              |
| `delete_site`                   | "Remove the old test site https://test.mywebsite.com from Search Console."                       |
| `get_search_analytics`          | "Show me the top 20 search queries for mywebsite.com in the last 30 days, highlight any with CTR below 2%, and suggest title improvements." |
| `get_performance_overview`      | "Create a visual performance overview of mywebsite.com for the last 28 days, identify any unusual drops or spikes, and explain possible causes." |
| `check_indexing_issues`         | "Check these important pages for indexing issues and prioritize which ones need immediate attention: mywebsite.com/product, mywebsite.com/services, mywebsite.com/about" |
| `inspect_url_enhanced`          | "Do a comprehensive inspection of mywebsite.com/landing-page and give me actionable recommendations to improve its indexing status." |
| `batch_url_inspection`          | "Inspect my top 5 product pages, identify common crawling or indexing patterns, and suggest technical SEO improvements." |
| `get_sitemaps`                  | "List all sitemaps for mywebsite.com, identify any with errors, and recommend next steps." |
| `list_sitemaps_enhanced`        | "Analyze all my sitemaps for mywebsite.com, focusing on error patterns, and create a prioritized action plan." |
| `submit_sitemap`                | "Submit my new product sitemap at https://mywebsite.com/product-sitemap.xml and explain how long it typically takes for Google to process it." |
| `get_sitemap_details`           | "Check the status of my main sitemap at mywebsite.com/sitemap.xml and explain what the warnings mean for my SEO." |
| `get_search_by_page_query`      | "What search terms are driving traffic to my blog post at mywebsite.com/blog/post-title? Identify opportunities to optimize for related keywords." |
| `compare_search_periods`        | "Compare my site's performance between January and February. What queries improved the most, which declined, and what might explain these changes?" |
| `get_advanced_search_analytics` | "Analyze queries with high impressions but positions below 10, filtered to mobile traffic in the US only. Use `filters` with country=usa and device=MOBILE." |

You can also ask your AI assistant to combine multiple tools and analyze the results. For example:

- "Find my top 20 landing pages by traffic, check their indexing status, and create a report highlighting any pages with both high traffic and indexing issues."

- "Analyze my site's performance trend over the last 90 days, identify my fastest-growing queries, and check if the corresponding landing pages have any technical issues."

- "Compare my desktop vs. mobile search performance, visualize the differences with charts, and recommend specific pages that need mobile optimization based on performance gaps."

- "Identify queries where I'm ranking on page 2 (positions 11-20) that have high impressions but low CTR, then inspect the corresponding URLs and suggest title and meta description improvements."

Your AI assistant will use the GSC tools to fetch the data, present it in an easy-to-understand format, create visualizations when helpful, and provide actionable insights based on the results.

---

## Data Visualization Capabilities

Your AI assistant can help you visualize your GSC data in various ways:

- **Trend Charts**: See how metrics change over time
- **Comparison Graphs**: Compare different time periods or dimensions
- **Performance Distributions**: Understand how your content performs across positions
- **Correlation Analysis**: Identify relationships between different metrics
- **Heatmaps**: Visualize complex datasets with color-coded representations

Simply ask your AI assistant to "visualize" or "create a chart" when analyzing your data, and it will generate appropriate visualizations to help you understand the information better.

---

## Troubleshooting

### Python Command Not Found

On macOS, the default Python command is often `python3` rather than `python`, which can cause issues with some applications including Node.js integrations.

If you encounter errors related to Python not being found, you can create an alias:

1. Create a Python alias (one-time setup):
   ```bash
   # For macOS users:
   sudo ln -s $(which python3) /usr/local/bin/python
   
   # If that doesn't work, try finding your Python installation:
   sudo ln -s /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 /usr/local/bin/python
   ```

2. Verify the alias works:
   ```bash
   python --version
   ```

This creates a symbolic link so that when applications call `python`, they'll actually use your `python3` installation.

### AI Client Configuration Issues

If you're having trouble connecting:

1. Make sure all file paths in your configuration are correct and use the full path
2. Check that your service account has access to your GSC properties
3. Restart your AI client after making any changes
4. Look for error messages in the response when you try to use a tool
5. Ensure your virtual environment is activated when running the server manually

### Other Unexpected Issues

If you encounter any other unexpected issues during installation or usage:

1. Copy the exact error message you're receiving
2. Use ChatGPT or Claude and explain your problem in detail, including:
   - What you were trying to do
   - The exact error message
   - Your operating system
   - Any steps you've already tried
3. AI assistants can often help diagnose and resolve technical issues by suggesting specific solutions for your situation

Remember that most issues have been encountered by others before, and there's usually a straightforward solution available.

---

## Related Tools

If you work with Google Search Console regularly, you may also find these tools useful:

**[Advanced GSC Visualizer](https://www.advancedgsc.com/)** — A Chrome extension (14,000+ users) that brings powerful charts, annotations, and one-click API access directly inside Google Search Console. Features include:

- Interactive charts with trendlines, moving averages, and Google algorithm update overlays
- One-click export of up to 25,000 rows from the GSC API — no coding required
- Keyword cannibalization detection
- Crawl stats visualizations
- AI assistant for querying your GSC data directly in the browser

Built by the same author. [Install from the Chrome Web Store →](https://chromewebstore.google.com/detail/advanced-gsc-visualizer/cdiccpnglfpnclonhpchpaaoigfpieel)

---

## Contributing

Found a bug or have an idea for improvement? We welcome your input! Open an issue or submit a pull request on GitHub.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

## Changelog

### [0.2.0] — March 2026

#### Added
- **Data freshness:** All search analytics queries now use `dataState: "all"` by default, returning data that matches the GSC dashboard instead of finalized-only data (which lags 2–3 days). Configurable via the `GSC_DATA_STATE` environment variable (`"all"` or `"final"`).
- **Flexible row limits:** `get_search_analytics` and `get_search_by_page_query` now accept an optional `row_limit` parameter (default 20, max 500). Claude will automatically choose an appropriate value based on your request — use higher values for comprehensive analysis, lower values for quick overviews.
- **Multi-dimension filtering:** `get_advanced_search_analytics` now accepts a `filters` parameter — a JSON array of filter objects for AND logic across multiple dimensions simultaneously (e.g., country = USA **and** device = mobile). The existing single-filter parameters (`filter_dimension`, `filter_operator`, `filter_expression`) remain fully supported.

### [0.2.1] — March 2026

#### Added
- **Reauthenticate tool:** New `reauthenticate` tool lets you switch Google accounts by deleting the saved OAuth token and triggering a fresh browser login. Ask your AI assistant: *"switch to a different Google account"*. (Thanks [@fterenzani](https://github.com/fterenzani)!)

#### Fixed
- **Sitemap TypeError crash:** `get_sitemaps` and `list_sitemaps_enhanced` crashed with `TypeError` when a sitemap had errors or warnings, because the GSC API returns those counts as strings. Added `int()` casts before comparison. (Thanks [@mcprobert](https://github.com/mcprobert)!)
- **File cache warning:** Suppressed the `file_cache is only supported with oauth2client<4.0.0` warning that caused crashes on MCP hosts that treat any stderr output as fatal (e.g. GitHub Copilot CLI).
- **Domain property 404 errors:** All tools now return a clear, actionable message when a 404 occurs, explaining the exact format required and service account permission requirements for `sc-domain:` properties.

#### Improved
- **Multi-client support:** README now explicitly lists Claude, Cursor, Codex, Gemini CLI, and Antigravity as supported clients with setup guidance for each.
- **`site_url` guidance:** All 15 tool docstrings now explain how to get the exact property URL from `list_properties` and how domain properties relate to subdomain filtering.

---

### [0.1.0] — Initial release

- 19 tools covering property management, search analytics, URL inspection, and sitemap management
- OAuth and service account authentication
- Batch URL inspection (up to 10 URLs)
- Period comparison tool
