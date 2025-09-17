# Instagram-chat-retreiver

A small command-line helper for downloading Instagram Direct conversation history via the Instagram Graph API. It can stream messages directly to the terminal or, when the optional [Textual](https://www.textualize.io/) dependency is installed, launch an interactive TUI for browsing conversations page by page.

## Features
- Fetches message pages for one or more Instagram conversation IDs using the Graph API v22.0.
- Normalises message payloads and surfaces human-readable text when available.
- Optional Textual interface that prompts for credentials and renders messages in a paginated table.
- Fallback CLI mode for printing either readable summaries or raw JSON payloads.

## Requirements
- Python 3.9 or newer.
- `requests` (required).
- `textual` (optional, enables the TUI; omit if you only need CLI output).

There is no `requirements.txt`; install the dependencies manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install requests textual  # textual is optional
```

If you skip the TUI, you can install only `requests`.

## Access Token & Permissions
You need an Instagram Graph API access token with sufficient permissions to read the target conversations (for example, `instagram_manage_messages`). The script accepts short-lived or long-lived tokens. Keep tokens private—avoid committing them to source control.

## Usage
Run the helper directly with Python:

```bash
python fetch_messages.py [OPTIONS] [CONVERSATION_ID ...]
```

When both an access token and at least one conversation ID are **not** supplied on the command line, the script launches the Textual prompt (if available) so you can enter them interactively.

### Common Options
- `--token TOKEN` – Instagram Graph API access token. If omitted, you will be prompted (when Textual is installed). Required for non-TUI mode.
- `--ids-file PATH` – Read conversation IDs from a file (one per line). IDs passed positionally are still accepted.
- `--order {asc,desc}` – Sort messages by `created_time` (default: `asc`).
- `--page-limit N` – Fetch at most *N* pages per conversation (0 means no limit).
- `--no-textual` – Force plain CLI output even if Textual is installed.
- `--raw` – Print raw JSON payloads instead of formatted summaries (requires `--no-textual`).

### Examples
Fetch two conversations in CLI mode (no TUI):

```bash
python fetch_messages.py 1234567890 2345678901 --token "$IG_TOKEN" --no-textual
```

Read conversation IDs from a file, paginate via the Textual UI:

```bash
python fetch_messages.py --token "$IG_TOKEN" --ids-file conversations.txt
```

Dump raw JSON for a single conversation, limiting to three pages:

```bash
python fetch_messages.py 1234567890 --token "$IG_TOKEN" --no-textual --raw --page-limit 3
```

### Textual Interface
When run with Textual installed (and without `--no-textual`):
1. If no token/IDs are provided, a prompt appears for entering them securely.
2. For each conversation ID, the app opens a tab with a paginated table showing timestamp, sender, and message text (when available).
3. Use the *Next Page* and *Prev Page* buttons to traverse the Graph API paging links.

If the Graph API request fails or no messages are returned, the CLI will emit an error or note accordingly.

## Troubleshooting
- **Authentication errors:** Ensure the token is valid and has messaging permissions for the connected Instagram Business account.
- **Missing Textual dependency:** Install `textual` or run with `--no-textual`.
- **Empty output:** Verify the conversation ID and that the authenticated user has access to the conversation history.

