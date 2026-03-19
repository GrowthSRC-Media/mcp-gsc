# MCP GSC Setup Guide (Agent Instructions)

This file is read automatically by AI coding agents (Codex, etc.) at the start of every session.
Walk the user through setting up the Google Search Console MCP server step by step.
Give exact commands to paste and wait for confirmation at each manual step.

---

## Behavior Rules

- Do NOT open new terminal windows programmatically.
- Print terminal commands inside code blocks for the user to paste.
- After each terminal step, verify the result yourself — do NOT trust "done" without checking.
- Derive all paths from cwd — never hard-code them.
- Windows only — use PowerShell/Windows syntax. Never use Unix syntax.
- Always reference `.venv\Scripts\python` and `.venv\Scripts\pip` directly; never ask the user to activate `.venv`.
- Do not run the MCP server manually. It is a stdio server launched by Claude Desktop. Running it in a terminal produces JSON parse errors — this is expected, not a bug.

---

## STEP 1 — Confirm project files

Check that these exist in cwd:
- `gsc_server.py` (Python entrypoint)
- `requirements.txt`

Read the server source to find the OAuth callback port/path. Tell the user what you found.

---

## STEP 2 — Virtual environment and dependencies

**2a.** If `.venv` does not exist:
```
python -m venv .venv
```

**2b.** Install dependencies:
```
.venv\Scripts\pip install -r requirements.txt
```

**2c.** Verify (run this yourself):
```
.venv\Scripts\python -c "import mcp; import google.auth; import googleapiclient; print('OK')"
```
- Output `OK` → proceed.
- `ModuleNotFoundError` → tell user install failed, ask them to re-run pip and paste full output.

---

## STEP 3 — Google Cloud Console setup

Print this block verbatim and wait for the user to reply "done":

   ─────────────────────────────────────────────────────
   MANUAL STEP — Google Cloud Console setup (do all of this now)

   ── Part A: Create or select a project ──────────────
   1. Go to https://console.cloud.google.com/
   2. Click the project dropdown → "New Project" (or pick an existing one)
   3. Name it (e.g. "GSC MCP") → click Create
   4. Make sure the new project is selected in the top dropdown

   ── Part B: Enable the Search Console API ───────────
   5. Left menu → APIs & Services → Library
   6. Search "Google Search Console API" → Enable
      (if it says "Manage" already, skip)

   ── Part C: Configure the OAuth consent screen ──────
   7. APIs & Services → OAuth consent screen
   8. User type: External → Create
   9. Fill in App name, support email, developer email → Save and Continue
   10. Add or Remove Scopes → add:
       https://www.googleapis.com/auth/webmasters.readonly
       → Update → Save and Continue
   11. Test Users → Add the Google account that has GSC access → Save and Continue
   12. Review → Back to Dashboard

   ── Part D: Create the OAuth 2.0 client ─────────────
   13. APIs & Services → Credentials
   14. + CREATE CREDENTIALS → OAuth 2.0 Client ID
   15. Application type: Desktop app → name it → CREATE
   16. DOWNLOAD JSON
       Google names it: client_secret_<long-id>.apps.googleusercontent.com.json
   17. Rename the file to exactly: client_secrets.json  ← note the "s"
   18. Move it into this project folder (replace any existing file)

   Reply "done" when finished.
   ─────────────────────────────────────────────────────

---

## STEP 4 — Verify client_secrets.json

After "done", check yourself:

1. Look for `client_secrets.json` in cwd.
2. If only `client_secret_*.json` exists (no "s"), tell the user to rename it.
3. Once found, read it and confirm it has a valid `client_id`.

---

## STEP 5 — Find the Claude Desktop config path

Run this PowerShell command and show output to user:
```powershell
Get-ChildItem "$env:LOCALAPPDATA\Packages" -Filter "Claude_*" -Directory | Select-Object -ExpandProperty FullName
```

- Folder found → config is at: `<that folder>\LocalCache\Roaming\Claude\claude_desktop_config.json`
- No folder → config is at: `%APPDATA%\Claude\claude_desktop_config.json`

Confirm the path with the user before writing.

---

## STEP 6 — Write the Claude Desktop config

1. Read the config file (if missing, start with `{ "mcpServers": {} }`).
2. Merge into `mcpServers` (keep all existing entries):
   ```json
   "gsc": {
     "command": "<absolute path to .venv\\Scripts\\python.exe>",
     "args": ["<absolute path to gsc_server.py>"]
   }
   ```
   Use double backslashes in JSON. Build paths from cwd.
3. Write the merged JSON back.
4. Print the complete final JSON and the full file path.

---

## STEP 7 — Browser account warning

Print verbatim:

   ─────────────────────────────────────────────────────
   BEFORE you restart Claude Desktop:

   Open your browser and sign into the Google account that has access
   to your GSC properties. If you have multiple accounts, switch to the
   correct one NOW — the active browser account at first launch is the
   one that gets authorized.

   To fix a wrong authorization later, use the "reauthenticate" tool
   inside Claude Desktop.
   ─────────────────────────────────────────────────────

---

## STEP 8 — Restart Claude Desktop

Tell the user:

1. Fully quit Claude Desktop (system tray → Quit, or Task Manager → End Task)
2. Reopen Claude Desktop
3. Approve the browser OAuth prompt with the correct account
4. In a new chat, type: `list_sites`
   - GSC properties listed → setup complete
   - "server not running" → go back to Step 2 and reinstall dependencies

---

## Expected session output

- `.venv` verified (`mcp`, `google.auth`, `googleapiclient` import OK)
- `client_secrets.json` present with valid `client_id`
- Final JSON written to `claude_desktop_config.json` (printed with full path)
- User reminded: correct browser account before restart, test with `list_sites`
