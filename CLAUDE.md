# MCP GSC Setup Guide

This file is read automatically by Claude Code at the start of every session.
Your job is to autonomously execute all automatable setup steps (venv, pip install, config file edits) yourself without asking the user to run commands. Only pause for steps that genuinely require the user's browser, Google account, or GUI interaction.

When the user says anything like "run the server", "set up the server", "start the server", or "configure GSC" — begin executing Steps 1–6 immediately and in sequence. Do NOT explain that the server is a stdio server and stop there. Instead, start doing the work: check files, create the venv, install deps, find the config path, write the config. Tell the user what you are doing at each step as you go.

---

## How to give commands to the user

- Do NOT try to open new terminal windows programmatically.
- When the user needs to run something in a terminal, print the exact command inside a code block and tell them to paste it.
- After each terminal step, verify the result yourself (run a check or read a file) — do NOT just trust the user's "done". If something is missing or failed, tell the user clearly and ask them to retry.

---

## STEP 1 — Confirm project files

Check that the following exist in cwd:
- Python entrypoint (e.g. `gsc_server.py`)
- `requirements.txt`
- Read the server source to find the OAuth callback port/path

Tell the user what you found.

---

## STEP 2 — Virtual environment and dependencies

Run all sub-steps yourself using the Bash tool — do NOT ask the user to run anything here.

**2a.** If `.venv` does not exist, run it yourself:
```
python -m venv .venv
```

**2b.** Run the dependency install yourself:
```
.venv\Scripts\pip install -r requirements.txt
```

**2c.** Verify the install yourself by running:
```
.venv\Scripts\python -c "import mcp; import google.auth; import googleapiclient; print('OK')"
```
- If output is `OK` — proceed.
- If it errors with `ModuleNotFoundError` — re-run the pip install command yourself and check again. If it still fails after retrying, show the user the error output and ask them to investigate their Python installation.

---

## STEP 3 — Google Cloud Console: full setup A to Z

Print this block verbatim and wait for the user to reply "done":

   ─────────────────────────────────────────────────────
   MANUAL STEP — Google Cloud Console setup (do all of this now)

   ── Part A: Create or select a project ──────────────
   1. Go to https://console.cloud.google.com/
   2. At the top, click the project dropdown → "New Project"
      (or select an existing project you want to use)
   3. Give it a name (e.g. "GSC MCP") and click Create
   4. Make sure the new project is selected in the top dropdown

   ── Part B: Enable the Search Console API ───────────
   5. In the left menu go to: APIs & Services → Library
   6. Search for "Google Search Console API"
   7. Click it → click "Enable"
      (if it already says "Manage", it's already enabled — skip)

   ── Part C: Configure the OAuth consent screen ──────
   8. Go to: APIs & Services → OAuth consent screen
   9. User type: select "External" → click Create
   10. Fill in:
       - App name: anything (e.g. "GSC MCP")
       - User support email: your email
       - Developer contact email: your email
   11. Click "Save and Continue"
   12. On the Scopes screen, click "Add or Remove Scopes"
       Search for and add:
       https://www.googleapis.com/auth/webmasters.readonly
       Click Update → Save and Continue
   13. On the Test Users screen, click "+ Add Users"
       Add the Google account email that has access to your GSC properties
       Click Save and Continue
   14. Review summary → click "Back to Dashboard"

   ── Part D: Create the OAuth 2.0 client ─────────────
   15. Go to: APIs & Services → Credentials
   16. Click "+ CREATE CREDENTIALS" → "OAuth 2.0 Client ID"
   17. Application type: Desktop app
   18. Name: anything (e.g. "GSC MCP Desktop") → click CREATE
   19. In the dialog that appears, click "DOWNLOAD JSON"
       NOTE: Google will download it with a long name like:
       client_secret_123456789-abc123.apps.googleusercontent.com.json
       You need to rename it.
   20. Rename the file to exactly: client_secrets.json
       (add the "s" — the server looks for this exact name)
   21. Move it into this project folder (replace any existing file)

   Reply "done" when finished.
   ─────────────────────────────────────────────────────

---

## STEP 4 — Verify client_secrets.json

After the user replies "done", do NOT trust it — check yourself:

1. List the files in cwd and look for `client_secrets.json`.
2. If you only see a file like `client_secret_*.json` (without the "s"), tell the user:
   "The file was downloaded but not renamed. Please rename it from `client_secret_*.json` to `client_secrets.json` and move it to this folder."
   Then wait for another "done" and re-check.
3. Once `client_secrets.json` exists, read it and confirm it contains a valid `client_id`.

---

## STEP 5 — Find the correct Claude Desktop config file

Claude Desktop on Windows can be installed two ways, each using a different config path:

- **Standard installer:** `%APPDATA%\Claude\claude_desktop_config.json`
  (expands to `C:\Users\<name>\AppData\Roaming\Claude\claude_desktop_config.json`)

- **Microsoft Store version:** `C:\Users\<name>\AppData\Local\Packages\Claude_<id>\LocalCache\Roaming\Claude\claude_desktop_config.json`

Run this PowerShell command yourself to detect the install type:
```powershell
Get-ChildItem "$env:LOCALAPPDATA\Packages" -Filter "Claude_*" -Directory | Select-Object -ExpandProperty FullName
```

- If a `Claude_*` folder is found → use `<that folder>\LocalCache\Roaming\Claude\claude_desktop_config.json`
- If no folder found → use `$env:APPDATA\Claude\claude_desktop_config.json`

Determine the path yourself and proceed directly to Step 6 — do NOT ask the user to confirm which path to use.

---

## STEP 6 — Write the Claude Desktop config

1. Read the config file at the correct path found in Step 5.
   - If it does not exist, start with: `{ "mcpServers": {} }`
   - If it exists and has a `"preferences"` key or other keys, keep them — only add/merge `"mcpServers"`

2. Merge this entry into `mcpServers` without removing other entries:
   ```json
   "gsc": {
     "command": "<absolute path to .venv\\Scripts\\python.exe>",
     "args": ["<absolute path to gsc_server.py>"]
   }
   ```
   Build both paths from cwd. Use double backslashes in JSON strings.

3. Write the merged JSON back to the config file.

4. Print the complete final JSON so the user can verify it.
   Also print the full file path so the user knows exactly where it was written.

---

## STEP 7 — Pre-launch browser instruction

Print this block verbatim:

   ─────────────────────────────────────────────────────
   BEFORE you restart Claude Desktop:

   Open your browser and make sure you are signed into the Google account
   that has access to your Google Search Console properties.

   If you have multiple Google accounts in the browser, switch to the
   correct one NOW — whichever account is active in the browser when
   Claude Desktop first starts the server is the one that gets authorized.

   If the wrong account gets authorized later, use the "reauthenticate"
   tool inside Claude Desktop to redo the OAuth flow.
   ─────────────────────────────────────────────────────

---

## STEP 8 — Restart Claude Desktop

Tell the user:

1. Fully quit Claude Desktop — right-click the system tray icon → Quit,
   OR open Task Manager → find Claude.exe → End Task
2. Reopen Claude Desktop
3. A browser tab will open asking to authorize Google Search Console — approve it with the correct account
4. In Claude Desktop, open a new chat and type: `list_sites`
   - If you see your GSC properties listed → setup is complete
   - If Claude Desktop says "server not running" → the dependencies may not have installed correctly. Go back to Step 2 and re-run the pip install.

---

## Rules

- Derive all paths from cwd — never hard-code them.
- Do not ask the user to activate `.venv` — always reference `.venv\Scripts\python` and `.venv\Scripts\pip` directly.
- For terminal commands Claude can run itself (e.g. venv creation, pip install, import checks), always run them directly using the Bash tool — do NOT ask the user to paste them.
- Only ask the user to run commands manually when it requires their credentials, browser interaction, or GUI actions that Claude cannot automate.
- Do not run the MCP server manually for testing. It is a stdio server launched by Claude Desktop automatically. Running it directly in a terminal will show JSON parse errors — this is expected and not a bug. "Server not running" in Claude Desktop means dependencies are missing, not that you need to run it manually.
- Windows only — use PowerShell/Windows syntax. Never use Unix syntax.
- Always verify results yourself after each step — do not trust "done" without checking.
- If any step fails, explain the error clearly and tell the user exactly what to do.

---

## Expected output at end of session

- Confirmation that `.venv` exists and imports verified (`mcp`, `google.auth`, `googleapiclient`)
- Confirmation that `client_secrets.json` is present with a valid `client_id`
- The exact JSON written to `claude_desktop_config.json`
- The full path of the config file that was actually used
- Reminder to have the correct browser account active before restarting Claude Desktop
- Reminder to restart Claude Desktop and test with `list_sites`
