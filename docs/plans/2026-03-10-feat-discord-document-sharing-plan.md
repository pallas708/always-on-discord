---
title: "feat: Discord Document Sharing via Custom MCP Server"
type: feat
status: active
date: 2026-03-10
---

# Discord Document Sharing via Custom MCP Server

## Enhancement Summary

**Deepened on:** 2026-03-10
**Sections enhanced:** All major sections
**Research agents used:** security-sentinel, architecture-strategist, performance-oracle, code-simplicity-reviewer, kieran-python-reviewer, pattern-recognition-specialist, agent-native-reviewer, framework-docs-researcher, best-practices-researcher, agent-native-architecture skill

### Key Improvements
1. **BLOCKER resolved**: FastMCP SDK requires Python >= 3.10; plan now includes Python upgrade step
2. **Critical security**: Added channel-ID validation, file deny list, and MCP-level rate limiting
3. **Agent-native**: Dynamic file manifest injection, vocabulary mapping, enriched error/success responses
4. **Performance**: Paginated `list_shareable_files`, streaming uploads via `aiohttp`, 429 retry logic
5. **Path validation hardened**: Proper directory boundary check (CVE-2025-53109 pattern), reject symlinks outright in v1

### New Considerations Discovered
- Discord REST API file uploads use `payload_json` field for structured message content alongside files
- `ToolError` from FastMCP SDK is the correct way to return client-visible errors
- Set `PYTHONUNBUFFERED=1` in MCP config to prevent stdio buffering
- System prompt section ordering matters: File Sharing must go between Memory and Security

## Overview

Add the ability for the Pallas bot to share files and generated content in Discord channels. A custom Python MCP server exposes file-sharing tools that Claude calls autonomously -- maintaining the existing architecture where Claude handles all Discord I/O through MCP. File access is restricted to configured directories with path traversal prevention, channel validation, size limits, and a sensitive-file deny list.

## Problem Statement / Motivation

The bot can currently only send text messages via `mcp__discord__discord_send`. Users in the Discord server cannot ask the bot to share documents (plans, brainstorms, code files, etc.) that exist on the host machine. The bot also cannot generate and share summaries, reports, or other content as downloadable files. The existing `mcp-discord` npm package does **not** support file attachments -- `discord_send` accepts only text content.

## Proposed Solution

Build a lightweight **custom MCP server** (`file_share_mcp/`) in Python that:

1. Exposes three tools: `list_shareable_files`, `send_file`, `send_generated_file`
2. Validates all file paths against an allowlist of directories
3. Validates channel IDs against the configured channel list
4. Uses the **Discord REST API** (not WebSocket) to upload files as attachments
5. Runs alongside the existing `mcp-discord` server, registered in `discord-mcp.json`

### Why a custom MCP server (not watcher relay or mcp-discord fork)

| Approach | Pros | Cons |
|----------|------|------|
| **Custom MCP server** (chosen) | Maintains MCP-only architecture; security validation at tool boundary; no watcher changes for I/O | New component to maintain |
| Watcher relay (Claude writes queue file) | Simple | Breaks "Claude does all Discord I/O via MCP" design; polling/file-watching complexity |
| Fork mcp-discord | Single MCP server | Maintenance burden of a fork; npm/Node.js for file ops when project is Python-first |

### Why REST API only (no WebSocket)

The system already has **two** Discord WebSocket connections (watcher.py via discord.py, and mcp-discord via Discord.js). A third would risk gateway conflicts. The Discord REST API handles file uploads without a persistent connection and uses the same bot token. REST calls do not require a gateway session.

## Technical Considerations

### Prerequisite: Python 3.10+ Upgrade

**BLOCKER**: The `mcp` Python SDK (FastMCP) requires Python >= 3.10. The project currently uses system Python 3.9.6. No version of the SDK ever supported 3.9.

**Resolution**: Install Python 3.12 via Homebrew and recreate the venv:

```bash
brew install python@3.12
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

All current dependencies (`discord.py>=2.3`, `pyyaml>=6.0`, `python-dotenv>=1.0`) support 3.12. Update `CLAUDE.md` to reflect the new Python version. Update the LaunchAgent plist if the venv Python path changes.

### Research Insights (Python version)

- `mcp` package v1.26.0 requires Python >= 3.10 (confirmed via PyPI and SDK source)
- Even v0.9.1 (oldest release) requires 3.10+
- The standalone `fastmcp` package (v3.1.0) also requires 3.10+
- The official `mcp` package is sufficient for this project (simpler than standalone `fastmcp`)
- With 3.12, modern type hint syntax (`str | None`, `list[str]`) works natively

### Architecture

```
+---------------------------------------------------------+
|  Claude CLI (persistent process)                         |
|  MCP Servers:                                            |
|  +-- discord (mcp-discord)      <- text messages          |
|  |   +-- discord_send, discord_read_messages, ...        |
|  +-- file-share (custom Python) <- file uploads           |
|  |   +-- list_shareable_files, send_file,                |
|  |       send_generated_file                             |
|  +-- (built-in file tools)      <- memory.json            |
+---------------------------------------------------------+
```

### Custom MCP Server: `file_share_mcp/`

A Python MCP server using the `mcp` SDK (FastMCP). Runs as a subprocess managed by Claude CLI via the MCP config, communicating over stdio (JSON-RPC).

**Server initialization:**

```python
from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP("FileSharing", mask_error_details=True)
```

- `mask_error_details=True` prevents leaking internal exception details to Claude. Only `ToolError` messages pass through; all other exceptions produce a generic "Internal error".
- Use `ToolError` for expected, client-visible errors (file not found, path rejected, size exceeded). Use Python exceptions for unexpected failures (disk error, network error).
- Use `Context` parameter for logging: `await ctx.info()`, `await ctx.warning()`.
- **Never use `print()`** -- it corrupts the stdio JSON-RPC stream. Log to stderr with `print("debug", file=sys.stderr)` or use `ctx`.

**Tools exposed:**

#### `list_shareable_files`

```
Parameters:
  - directory: str (optional) -- subdirectory path relative to project root; if omitted, lists all allowed dirs
  - pattern: str (optional) -- glob pattern to filter (e.g., "*.md", "*.pdf")
  - max_results: int (optional, default 50) -- maximum files to return (hard cap: 200)
  - include_metadata: bool (optional, default false) -- if true, includes size_bytes and modified timestamp per file; if false, returns only path and name (avoids O(n) stat() syscalls and reduces context tokens)

Returns:
  - When include_metadata=false (default):
    {files: [{path, name}], truncated: bool, total_available: int}
  - When include_metadata=true:
    {files: [{path, name, size_bytes, modified}], truncated: bool, total_available: int}

Security:
  - Only lists files within configured allowed_paths
  - Resolves symlinks before checking containment; rejects symlinks in v1
  - max_results hard cap prevents context window exhaustion (200 files ~= 5000 tokens)
```

#### Research Insights (list_shareable_files)

- Without pagination, this tool can exhaust Claude's context window. At 1000 files with ~100 bytes metadata each, that's ~25,000 tokens injected into context, accelerating rotation.
- `os.stat()` per file is O(n) syscalls. Consider deferring metadata: return only `path` and `name` by default, with an optional `include_metadata: bool` parameter.
- Returning a `truncated` flag lets Claude decide whether to narrow its search with a more specific pattern.

**Validation flow for `list_shareable_files`** (explicit): The `directory` parameter, if provided, goes through the same `validate_path()` function as `send_file`. This ensures that even browsing is restricted to allowed paths. The validation flow is:
1. If `directory` is provided, call `validate_path(directory, allowed_paths, project_root)` -- rejects paths outside allowed dirs
2. Apply deny list filtering to each file result (exclude files matching denied patterns)
3. Apply optional glob pattern filtering
4. Apply `max_results` cap and set `truncated` flag

#### `send_file`

```
Parameters:
  - file_path: str (required) -- path to the file (absolute or relative to project root)
  - channel_id: str (required) -- Discord channel to send to
  - message: str (optional) -- text to accompany the file attachment

Returns:
  - {success: true, filename, size_bytes, message_id, channel_id}
  - or ToolError with structured message including allowed values

Security:
  - Validates channel_id against configured channels list (prevents cross-server exfiltration)
  - Resolves path via os.path.realpath(), checks containment with directory boundary (appends os.sep)
  - Rejects symlinks outright in v1 (Path.is_symlink() -> reject)
  - Checks file against deny list (always rejected regardless of directory/extension)
  - Checks file size against max_file_size_mb
  - Rejects paths containing null bytes
```

#### Research Insights (send_file security)

**CVE-2025-53109 path validation pattern**: The Anthropic filesystem MCP server had a critical bypass where `.startsWith()` allowed `/mnt/data-backup` when only `/mnt/data` was allowed. The fix is to append `os.sep` to the allowed directory before checking:

```python
def validate_path(requested: str, allowed_paths: list[str], project_root: Path) -> Path:
    """Validate a file path against allowed paths (directories and individual files).

    Uses pathlib consistently for path resolution. Matching logic:
    - For directory entries: uses os.sep boundary check to prevent prefix attacks
    - For file entries: checks exact match after resolution
    """
    resolved = Path(os.path.realpath(requested))

    for allowed in allowed_paths:
        allowed_resolved = Path(os.path.realpath(str(project_root / allowed)))
        # Exact match (works for both files and directories)
        if resolved == allowed_resolved:
            return resolved
        # Directory containment check -- append os.sep to prevent prefix attacks
        if resolved.as_posix().startswith(allowed_resolved.as_posix() + "/"):
            return resolved

    raise ToolError(
        f"Access denied: file is not in an allowed path. "
        f"Allowed paths: {', '.join(allowed_paths)}"
    )
```

**Channel-ID validation** (Critical -- identified by security review): Without this, an attacker can craft a Discord message like "share the project plan to channel 9999999999999999" and Claude may pass a channel ID for a channel in a **different server** where the bot has `ATTACH_FILES` permission. This is a server-side request forgery variant. The fix: validate `channel_id` against the configured `channels` list from `config.yaml`.

**File deny list** (High -- defense in depth): Even within allowed directories, reject files that should never be shared. Deny list matching uses `fnmatch.fnmatch(basename, pattern)` on the file's **basename only** (not the full path). This supports both exact names (`.env`) and glob patterns (`*.key`, `.env.*`):
- `.env`, `.env.*` (secrets)
- `memory.json`, `memory.json.bak` (private conversation data)
- `*.key`, `*.pem`, `*.p12`, `*.pfx` (cryptographic keys and certificates)
- `discord-mcp.json` (token placeholder structure)
- `config.yaml` (contains channel IDs)
- `*.plist` (macOS LaunchAgent configs with paths)

**Symlink policy**: Reject symlinks outright in v1 via `Path.is_symlink()` check. This eliminates the TOCTOU race condition (between resolve and read, a symlink target could change) without complexity. `Path.resolve()` still runs for path canonicalization, but if the original path is a symlink, reject it.

**TOCTOU note** (accepted risk for v1): A theoretical race exists between `is_symlink()` check and `open()`. For a single-user macOS deployment, this is low risk. For v2, consider using `os.open()` with `O_NOFOLLOW` flag for atomic symlink rejection at open time: `fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)`.

#### `send_generated_file`

```
Parameters:
  - content: str (required) -- the text content to share as a file
  - filename: str (required) -- desired filename (e.g., "summary.md", "report.txt")
  - channel_id: str (required) -- Discord channel to send to
  - message: str (optional) -- text to accompany the file attachment

Returns:
  - {success: true, filename, size_bytes, message_id, channel_id}
  - or ToolError with structured message

Security:
  - Validates channel_id against configured channels list
  - Sanitizes filename with strict regex: ^[a-zA-Z0-9_-]{1,100}\.[a-z]{1,5}$
  - Checks generated content size against max_file_size_mb
  - Only allows text-based extensions (.md, .txt, .py, .json, .yaml, .csv)
  - Writes to app-specific temp directory with 0o700 permissions
  - Uses tempfile.NamedTemporaryFile(delete=False) with explicit cleanup in finally block
```

#### Research Insights (send_generated_file)

**Tempfile pattern** -- the correct implementation:

```python
import tempfile
import os

TEMP_DIR = Path(PROJECT_ROOT) / "tmp"
TEMP_DIR.mkdir(mode=0o700, exist_ok=True)

tmp_path: Path | None = None
try:
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=f"_{sanitized_filename}",
        dir=str(TEMP_DIR),
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    # File is flushed and closed; safe to upload
    await upload_file_to_discord(...)
finally:
    if tmp_path and tmp_path.exists():
        tmp_path.unlink()
```

- Use `delete=False` because the file must remain on disk until the async upload completes
- Use an app-specific temp directory (`PROJECT_ROOT/tmp/` with `0o700`), not the world-writable `/tmp`
- Add `tmp/` to `.gitignore`
- On MCP server startup, delete **all** files in `tmp/` (not just files older than 1 hour). The `tmp/` directory is exclusively owned by this MCP server, so any files present at startup are orphans from a previous crash. This is safe because the MCP server is the only writer.

**Composability note**: `send_generated_file` is a convenience shortcut that bundles creation + upload. Claude can also achieve the same outcome by using `Write` to save content to an allowed directory, then calling `send_file`. The system prompt should document both paths.

### Discord REST API for File Upload

The MCP server uses `aiohttp` (definitively -- not `requests`) to call the Discord REST API. Using synchronous `requests` inside a FastMCP async tool handler would block the event loop during uploads.

#### Research Insights (Discord upload)

**Use `payload_json` for structured messages with attachments:**

```python
import aiohttp
import json

# Module-level session -- created once, reused across uploads.
# Fixes: creating a new ClientSession per upload defeats connection pooling
# and adds ~150ms TLS handshake overhead per call.
_http_session: aiohttp.ClientSession | None = None

def get_http_session() -> aiohttp.ClientSession:
    """Return a shared aiohttp session, creating it on first use."""
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
    return _http_session

async def upload_file_to_discord(
    token: str,
    channel_id: str,
    file_path: Path,
    message: str | None = None,
    max_retries: int = 1,
) -> dict:
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}"}

    # Read file into memory to avoid file handle leaks and EOF-on-retry bugs.
    # Safe for files under 10MB cap.
    file_data = file_path.read_bytes()

    session = get_http_session()

    for attempt in range(1 + max_retries):
        # Rebuild FormData on each attempt -- FormData objects are single-use
        # because their internal stream position is not reset after consumption.
        form = aiohttp.FormData()
        form.add_field(
            "payload_json",
            json.dumps({
                "content": message or "",
                "attachments": [{"id": 0, "filename": file_path.name}],
            }),
            content_type="application/json",
        )
        form.add_field(
            "files[0]",
            file_data,
            filename=file_path.name,
            content_type="application/octet-stream",
        )

        async with session.post(url, headers=headers, data=form) as resp:
            if resp.status == 429 and attempt < max_retries:
                # Handle rate limit -- parse retry_after from JSON body,
                # falling back to Retry-After header (Cloudflare may return
                # non-JSON 429 responses).
                try:
                    data = await resp.json()
                    retry_after = data.get("retry_after", 1.0)
                except (aiohttp.ContentTypeError, json.JSONDecodeError):
                    retry_after = float(resp.headers.get("Retry-After", "1.0"))
                await asyncio.sleep(retry_after)
                continue
            resp.raise_for_status()
            return await resp.json()
```

**Rate limit handling:**
- Parse `X-RateLimit-Remaining` header preemptively; don't wait for 429s
- On 429, use `retry_after` from the JSON body (float with sub-second precision)
- Global rate limit: 50 requests/second across all endpoints
- Channel message endpoint: ~5 requests per 5 seconds per channel
- If 10,000 failed requests (401/403/429) in 10 minutes, Discord issues a temporary IP ban

**Error response sanitization**: Map Discord error codes to generic user-facing messages before returning to Claude:
- 403 -> "I don't have permission to upload files to that channel"
- 413 -> "The file is too large for Discord (limit: {max_size_mb}MB)"
- 429 -> "Rate limited, please try again shortly"
- 5xx -> "Discord is temporarily unavailable"

### Configuration Changes

Add `file_sharing` section to `config.yaml`:

```yaml
file_sharing:
  allowed_paths:        # renamed from allowed_directories; accepts both dirs and files
    - docs              # relative to project root (directory)
    - persona.md        # individual files also allowed
  max_file_size_mb: 10
  denied_files:         # always rejected regardless of directory
    # Matching uses fnmatch (basename-only): fnmatch.fnmatch(filename, pattern)
    # Exact names and glob patterns are both supported.
    - .env
    - .env.*            # .env.local, .env.production, etc.
    - memory.json
    - memory.json.bak
    - discord-mcp.json
    - config.yaml       # contains channel IDs
    - "*.key"           # cryptographic private keys
    - "*.pem"           # certificates / private keys
    - "*.p12"           # PKCS#12 key bundles
    - "*.pfx"           # PKCS#12 (Windows naming)
    - "*.plist"         # macOS LaunchAgent configs
```

#### Research Insights (config)

- The naming convention `snake_case` is consistent with existing config keys (`max_turns`, `mcp_config`).
- Extension filtering was evaluated and removed for v1 -- if a file is in an allowed directory, it is shareable. The deny list provides defense in depth against sensitive files without the maintenance burden of a 10-entry extension allowlist.
- `max_file_size_mb` defaults to 10 (Discord's default limit for unboosted servers). Making it configurable supports boosted servers.
- **Mixed files and directories**: The validation logic must handle both cases. For directory entries (e.g., `docs`), check `is_relative_to()`. For individual file entries (e.g., `persona.md`), check exact match after resolution.
- **Channel ID type conversion**: YAML stores channel IDs as integers (e.g., `1480585939687313549`), but MCP tool parameters define `channel_id: str` and `FileSharingConfig` types them as `list[str]`. The config loader **must** convert explicitly: `allowed_channels = [str(ch) for ch in config["channels"]]`. Without this, validation always fails (`"1480585939687313549" != 1480585939687313549`).
- **`FileSharingConfig.allowed_channels`** is populated from the **root-level** `channels` key (not from `file_sharing`), since channel validation is shared across file-sharing and the watcher.

### MCP Config Changes

Add the custom server to `discord-mcp.json`:

```json
{
  "mcpServers": {
    "discord": {
      "command": "npx",
      "args": ["-y", "mcp-discord"],
      "env": { "DISCORD_TOKEN": "" }
    },
    "file-share": {
      "command": "/Users/pallas/Documents/always_on_discord/.venv/bin/python",
      "args": ["-m", "file_share_mcp"],
      "env": {
        "DISCORD_TOKEN": "",
        "FILE_SHARE_CONFIG": "/Users/pallas/Documents/always_on_discord/config.yaml",
        "FILE_SHARE_PROJECT_ROOT": "/Users/pallas/Documents/always_on_discord",
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
```

#### Research Insights (MCP config)

- **`PYTHONUNBUFFERED=1`** is required to prevent Python's output buffering from interfering with stdio JSON-RPC transport.
- **`FILE_SHARE_CONFIG`** passes the config path explicitly -- the MCP server cannot assume its cwd is the project root.
- **`FILE_SHARE_PROJECT_ROOT`** anchors all relative `allowed_paths` entries to a known root, preventing containment checks from depending on the subprocess's cwd (which is set by Claude CLI, not by us).
- The bot token is injected into both MCP servers' environments by `watcher.py`'s `_build_env()` method (existing pattern).
- The hardcoded venv path is acceptable for a single-deployment personal project but should have a comment noting it is machine-specific. **After Phase 0 Python upgrade**, verify the venv Python path in `discord-mcp.json` still resolves correctly (it should, since `python3.12 -m venv .venv` creates `.venv/bin/python` at the same location).

### ALLOWED_TOOLS Update

Add to `watcher.py` `ALLOWED_TOOLS`:

```python
"mcp__file-share__list_shareable_files",
"mcp__file-share__send_file",
"mcp__file-share__send_generated_file",
```

#### Research Insights (naming)

- The `mcp__{server-name}__{tool_name}` pattern is correctly followed. The hyphen in `file-share` matches the MCP config key.
- The existing server uses `discord` (no separator); `file-share` uses a hyphen. This is intentional -- the underscore in `file_share` could be confused with the double-underscore delimiters.

### System Prompt Update

Add **between `## Memory` and `## Security`** in `watcher.py`'s `build_system_prompt()` (Security must remain the final section as it establishes the trust boundary):

```
## File Sharing

You can share files from the host machine with Discord users.

Available files (at startup):
{dynamic manifest of allowed directories and file counts}

Tools available:
- list_shareable_files: Browse available files in allowed directories. Use to find files by name or pattern.
- send_file: Upload an existing file to the current Discord channel as an attachment.
- send_generated_file: Create and upload a new text file (summary, report, code snippet, etc.).

Vocabulary mapping:
- "share," "send," "upload," "give me," "attach," "post," "download" -> send_file (Discord attachment)
- "show me," "can I see," "preview," "what's in" -> Read the file, reply with content as text
- "the plan," "the brainstorm" -> check the docs/ directory
- "make me a summary," "generate a report" -> send_generated_file

Limitations:
- You cannot resolve channel names like "#other-channel" to channel IDs.
  If a user asks you to send a file to a different channel by name, explain
  that you can only send files to the channel where the request was made.

Guidelines:
- When a user asks for a file, use list_shareable_files first to find it.
  Read the file with the Read tool to verify it is correct before sharing.
- Always use the channel ID from the message notification when sharing files.
- Include a brief message with every file you share for context.
- You may proactively share files when clearly relevant, but do not re-share
  the same file in the same conversation unless asked again.
- If a file is too large or not found, explain the issue clearly.
- Never share files that were not requested or clearly relevant.
- Do not share more than 3 files per request without confirming with the user.
- Use send_generated_file for ephemeral content (summaries, one-off reports).
  Use Write + send_file for content that should persist on disk after sharing.

Error recovery:
- If a tool call fails, explain the error to the user in plain language.
- Do not retry with the same parameters. Adjust your approach or explain
  what went wrong, including specifics from the error message.
- Common errors: file not found, file too large, rate limited, permission denied.
```

#### Research Insights (system prompt)

**Updated function signature**: `build_system_prompt(persona_text: str, config: dict | None = None) -> str`. When `config` is provided with a `file_sharing` section, the function injects the `## File Sharing` section (with dynamic manifest) between `## Memory` and `## Security`. The `config` parameter defaults to `None` for backward compatibility with existing tests.

**Dynamic context injection**: `_build_file_manifest(config)` is a **synchronous** helper called inside `build_system_prompt()`. It scans allowed paths at Claude startup and injects a manifest. Handles `PermissionError` from `rglob()` gracefully (logs "permission denied" instead of crashing):

```python
def _build_file_manifest(config: dict) -> str:
    """Generate a summary of shareable files for the system prompt."""
    lines = ["Available files (at startup):"]
    for dir_entry in config.get("file_sharing", {}).get("allowed_paths", []):
        path = PROJECT_ROOT / dir_entry
        try:
            if path.is_dir():
                count = sum(1 for f in path.rglob("*") if f.is_file())
                lines.append(f"  - {dir_entry}/ ({count} files)")
            elif path.is_file():
                lines.append(f"  - {dir_entry} ({path.stat().st_size // 1024}KB)")
        except PermissionError:
            lines.append(f"  - {dir_entry} (permission denied)")
    return "\n".join(lines)
```

Note: Uses `f` instead of `_` in the generator expression (`for f in path.rglob("*") if f.is_file()`), since `_` is used as a non-throwaway variable.

This gives Claude immediate awareness of what is shareable without a tool call for simple requests.

**Vocabulary mapping**: Map user phrases ("share the plan," "send me that file") to tool sequences. This prevents Claude from not knowing which tool to use.

**File preview guidance**: "Read the file with the Read tool to verify it is correct before sharing." This addresses the agent-native gap where Claude might share the wrong file without previewing.

**Rate-limiting guidance**: "Do not share more than 3 files per request" bounds the blast radius of prompt injection attempting bulk exfiltration.

### Bot Permissions

The Discord bot requires the `ATTACH_FILES` permission in addition to existing permissions. This must be enabled in the Discord Developer Portal and the bot re-invited if necessary.

The existing plan document lists permissions as "read/send messages, read history, embed links" -- `ATTACH_FILES` must be added.

## System-Wide Impact

- **Interaction graph**: User message -> watcher -> Claude -> `list_shareable_files` (MCP) -> `send_file` (MCP) -> Discord REST API. No new callbacks or middleware. The watcher is not involved in file upload I/O.
- **Error propagation**: MCP tool errors surface to Claude as `ToolError` results (with `isError: true`). Claude communicates the error to the user via `discord_send` from the still-functioning `mcp-discord` server. Discord REST API errors are caught by the MCP server, sanitized (no raw error details), and returned as structured error messages.
- **MCP server crash path**: If the file-share MCP server dies, Claude CLI returns tool errors for file-share tool calls. Text messaging continues via the existing `mcp-discord` server. The file-share server restarts on the next Claude CLI restart (context rotation or crash recovery).
- **State lifecycle risks**: Generated content temp files are the only new state. Cleaned up in `finally` blocks and on server startup (orphan cleanup). No database, no shared mutable state. The MCP server is stateless between tool calls.
- **API surface parity**: The existing `discord_send` (text-only) is unchanged. File sharing is additive.
- **Context rotation**: File uploads via REST API complete within seconds. The existing rotation grace period (close stdin, wait 30s) is sufficient. No special handling needed.
- **Restart chain**: Config changes require a watcher restart -> stops Claude CLI -> stops both MCP servers. On watcher restart, Claude CLI re-spawns -> re-spawns both MCP servers with fresh config.

## Acceptance Criteria

### Functional

- [ ] `list_shareable_files` returns files only from configured `allowed_paths`
- [ ] `list_shareable_files` respects `max_results` parameter with hard cap at 200
- [ ] `list_shareable_files` returns `truncated` flag when results are capped
- [ ] `list_shareable_files` supports optional glob pattern filtering
- [ ] `send_file` uploads a file to the correct Discord channel as an attachment
- [ ] `send_file` includes optional accompanying text message
- [ ] `send_file` returns Discord message ID in success response
- [ ] `send_file` validates `channel_id` against configured channels list
- [ ] `send_file` rejects files outside allowed directories (returns clear error with allowed dirs)
- [ ] `send_file` rejects files on the deny list (`.env`, `memory.json`, etc.)
- [ ] `send_file` rejects files exceeding `max_file_size_mb`
- [ ] `send_file` prevents path traversal (`../../.env` resolves and is rejected)
- [ ] `send_file` rejects symlinks outright (`Path.is_symlink()` -> reject)
- [ ] `send_file` uses proper directory boundary check (append `os.sep` -- CVE-2025-53109 pattern)
- [ ] `send_generated_file` creates a temp file in app-specific temp dir, uploads it, and cleans up
- [ ] `send_generated_file` sanitizes filename with strict regex (`^[a-zA-Z0-9_-]{1,100}\.[a-z]{1,5}$`)
- [ ] `send_generated_file` validates `channel_id` against configured channels list
- [ ] Bot responds to user requests like "share the plan" by finding and uploading the file
- [ ] Bot previews files (via Read) before sharing to verify correctness
- [ ] Bot can proactively share files when contextually relevant
- [ ] Bot responds clearly when a requested file is not found
- [ ] Config changes to `allowed_paths` take effect on watcher restart
- [ ] Orphaned temp files cleaned up on MCP server startup

### Non-Functional

- [ ] Custom MCP server uses Discord REST API only (no WebSocket connection)
- [ ] Bot token passed via environment variable, never hardcoded or logged
- [ ] File share operations are logged via `Context` (file path, channel, success/failure)
- [ ] `ATTACH_FILES` permission documented as required
- [ ] `ToolError` used for expected errors; `mask_error_details=True` for unexpected errors
- [ ] `PYTHONUNBUFFERED=1` set in MCP config env
- [ ] Discord API 429 responses handled with retry-after backoff
- [ ] Tests follow `unittest.TestCase` pattern consistent with `tests/test_watcher.py`

## Success Metrics

- Users can request and receive files from the bot in Discord
- Path traversal, channel validation, and deny list tests pass with 100% coverage
- No sensitive files (`.env`, credentials, `memory.json`) are exposable through the tool
- Generated file temp cleanup has no leaks (verified by test and startup cleanup)
- Error responses include actionable context (allowed directories, size limits)

## Dependencies & Risks

| Dependency | Status | Notes |
|-----------|--------|-------|
| Python 3.10+ | **BLOCKER -- To install** | `brew install python@3.12`, recreate `.venv` |
| `mcp` Python SDK (FastMCP) | To install | `pip install mcp` (v1.26.0+) |
| `aiohttp` | To install | For async Discord REST API calls (not `requests`) |
| Discord bot `ATTACH_FILES` permission | To verify | May need to re-invite bot with updated permissions |
| `requirements.txt` update | To do | Add `mcp` and `aiohttp` |

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Discord REST API rate limit on uploads | Low | Uploads throttled | 429 retry-with-backoff; MCP-level rate limit (10 ops/min global, 5/min per channel) |
| MCP server startup failure | Low | No file sharing | Watcher logs MCP errors; text messaging still works via existing MCP server |
| Generated content exceeds size limit | Low | Upload rejected | Check content size before writing temp file; return clear error with limit |
| User requests file with ambiguous name | Medium | Wrong file shared | System prompt instructs Claude to preview with Read before sharing |
| Prompt injection for bulk file exfiltration | Medium | All allowed files shared | Channel validation + rate limiter + max 3 files per request guidance |
| Cross-server file delivery via channel_id | Medium | Files sent to attacker-controlled channel | Channel-ID validation against configured channels list |
| Python 3.12 upgrade breaks existing code | Low | Watcher fails | All current deps support 3.12; test after upgrade |

## Implementation Phases

### Phase 0: Python Upgrade (Prerequisite)

- [x] Install Python 3.12: `brew install python@3.12`
- [x] Recreate venv: `python3.12 -m venv .venv`
- [x] Install existing deps: `.venv/bin/pip install -r requirements.txt`
- [x] Verify watcher still works: `python -m pytest tests/ -v`
- [x] Add `mcp` and `aiohttp` to `requirements.txt`
- [x] Install new deps: `.venv/bin/pip install -r requirements.txt`
- [x] Update `CLAUDE.md` to reflect Python 3.12

### Phase 1: MCP Server Scaffold & Security Layer

**Tests first** (`tests/test_file_share_mcp.py`):

**Test pattern guidance:**
- Security and validation tests use synchronous `unittest.TestCase` (no async needed)
- Upload and tool handler tests use `unittest.IsolatedAsyncioTestCase` for async functions (`aiohttp` uploads, FastMCP tool handlers). Available in stdlib since Python 3.8.
- Use `TestFileSharingConfig` (not `TestConfig`) to avoid collision with `tests/test_watcher.py::TestConfig`

Tests should use bare `assert` statements, `unittest.mock`, and in-method imports -- consistent with `tests/test_watcher.py`.

**Synchronous tests** (`unittest.TestCase`):

- [x] `TestPathValidation`: allowed directory containment with `os.sep` boundary check
- [x] `TestPathValidation`: traversal rejection (`../`, `../../.env`)
- [x] `TestPathValidation`: symlink rejection (`Path.is_symlink()`)
- [x] `TestPathValidation`: null byte rejection
- [x] `TestPathValidation`: directory boundary attack (`docs-backup` when only `docs` allowed)
- [x] `TestPathValidation`: mixed files and directories in allowed_paths
- [x] `TestDenyList`: `.env`, `memory.json`, `memory.json.bak`, `discord-mcp.json` always rejected
- [x] `TestChannelValidation`: allowed channel accepted, disallowed channel rejected
- [x] `TestSizeLimit`: file at/above limit rejected, below limit accepted
- [x] `TestFilenameSanitization`: regex allowlist for generated filenames
- [x] `TestTempFileCleanup`: cleanup in finally block, even on upload failure
- [x] `TestTempFileCleanup`: orphan cleanup on server startup
- [x] `TestFileSharingConfig`: loading `file_sharing` section from config.yaml (renamed from `TestConfig`)
- [x] `TestListShareableFiles`: max_results cap, truncated flag, pattern filtering
- [x] `TestRateLimiter`: global limit (10/min) blocks after 10 ops
- [x] `TestRateLimiter`: per-channel limit (5/min) blocks after 5 ops on same channel
- [x] `TestRateLimiter`: requests succeed after window expires
- [x] `TestRateLimiter`: returns `ToolError` when rate limited

**Implementation:**

- [x] Create `file_share_mcp/` package with `__init__.py` and `__main__.py`
- [x] `file_share_mcp/__main__.py` -- FastMCP server entry point, tool definitions, startup cleanup

```python
# file_share_mcp/__main__.py
from mcp.server.fastmcp import FastMCP, Context
mcp = FastMCP("FileSharing", mask_error_details=True)

def cleanup_stale_temps() -> None:
    """Delete ALL files in tmp/ on startup. Synchronous -- runs before mcp.run() event loop.

    The tmp/ directory is exclusively owned by this MCP server. Any files
    present at startup are orphans from a previous crash or interrupted upload.
    """
    tmp_dir = TEMP_DIR  # PROJECT_ROOT / "tmp"
    if tmp_dir.exists():
        for f in tmp_dir.iterdir():
            if f.is_file():
                f.unlink()
                print(f"Cleaned up orphaned temp file: {f.name}", file=sys.stderr)

# ... tool definitions ...

if __name__ == "__main__":
    cleanup_stale_temps()  # sync; runs before event loop starts
    mcp.run(transport="stdio")
```

- [x] `file_share_mcp/rate_limiter.py` -- sliding-window rate limiter (global 10/min, per-channel 5/min)

```python
# file_share_mcp/rate_limiter.py
import time
from mcp.server.fastmcp import ToolError

class RateLimiter:
    """Sliding-window rate limiter for file sharing operations.

    Enforces two limits:
    - Global: max_global ops per window across all channels
    - Per-channel: max_per_channel ops per window per channel_id
    Returns ToolError when limit exceeded.
    """

    def __init__(
        self,
        max_global: int = 10,
        max_per_channel: int = 5,
        window_seconds: float = 60.0,
    ):
        self.max_global = max_global
        self.max_per_channel = max_per_channel
        self.window_seconds = window_seconds
        self._global_timestamps: list[float] = []
        self._channel_timestamps: dict[str, list[float]] = {}

    def _prune(self, timestamps: list[float], now: float) -> list[float]:
        cutoff = now - self.window_seconds
        return [t for t in timestamps if t > cutoff]

    def check(self, channel_id: str) -> None:
        """Check rate limits. Raises ToolError if limit exceeded."""
        now = time.monotonic()

        # Prune and check global limit
        self._global_timestamps = self._prune(self._global_timestamps, now)
        if len(self._global_timestamps) >= self.max_global:
            raise ToolError(
                f"Rate limited: max {self.max_global} file operations per minute globally. "
                f"Please wait before trying again."
            )

        # Prune and check per-channel limit
        ch_ts = self._channel_timestamps.get(channel_id, [])
        ch_ts = self._prune(ch_ts, now)
        if len(ch_ts) >= self.max_per_channel:
            raise ToolError(
                f"Rate limited: max {self.max_per_channel} file operations per minute per channel. "
                f"Please wait before trying again."
            )

        # Record this operation
        self._global_timestamps.append(now)
        ch_ts.append(now)
        self._channel_timestamps[channel_id] = ch_ts
```

- [x] `file_share_mcp/security.py` -- path validation, channel validation, deny list, size checks
- [x] `file_share_mcp/config.py` -- load config, typed `FileSharingConfig` dataclass

```python
from dataclasses import dataclass, field
from pathlib import Path

@dataclass(frozen=True)
class FileSharingConfig:
    allowed_paths: list[Path]          # renamed from allowed_directories; holds both dirs and files
    allowed_channels: list[str]        # populated from root-level config["channels"], converted to str
    denied_files: list[str]            # fnmatch patterns, matched against basename only
    max_file_size_bytes: int
    project_root: Path
    temp_dir: Path

    def __post_init__(self):
        """Validate required fields at construction time."""
        if self.max_file_size_bytes <= 0:
            raise ValueError("max_file_size_bytes must be positive")
        if not self.project_root.exists():
            raise ValueError(f"project_root does not exist: {self.project_root}")
        if not self.allowed_channels:
            raise ValueError("allowed_channels must not be empty")

    @classmethod
    def from_config(cls, config: dict, project_root: Path) -> "FileSharingConfig":
        """Build from parsed config.yaml dict. Handles int-to-str channel ID conversion."""
        fs = config.get("file_sharing", {})
        return cls(
            allowed_paths=[
                project_root / p for p in fs.get("allowed_paths", [])
            ],
            # Convert int channel IDs from YAML to str for MCP tool parameter matching
            allowed_channels=[str(ch) for ch in config["channels"]],
            denied_files=fs.get("denied_files", []),
            max_file_size_bytes=fs.get("max_file_size_mb", 10) * 1024 * 1024,
            project_root=project_root,
            temp_dir=project_root / "tmp",
        )
```

- [x] Implement `list_shareable_files` tool with `max_results` pagination
- [x] Implement path validation with proper directory boundary check
- [x] Create `tmp/` directory, add to `.gitignore`

### Phase 2: Discord Upload & Tool Integration

**Tests first** (async tests use `unittest.IsolatedAsyncioTestCase`):

- [x] `TestDiscordUpload(IsolatedAsyncioTestCase)`: REST API upload with mocked `aiohttp` session
- [x] `TestDiscordUpload(IsolatedAsyncioTestCase)`: 429 rate limit retry-with-backoff
- [x] `TestDiscordUpload(IsolatedAsyncioTestCase)`: error response sanitization (403, 413, 5xx)
- [x] `TestSendFile(IsolatedAsyncioTestCase)`: end-to-end with mocked Discord API, verify message_id in response
- [x] `TestSendGeneratedFile(IsolatedAsyncioTestCase)`: end-to-end with mocked Discord API
- [x] `TestSendFile(IsolatedAsyncioTestCase)`: accompanying message parameter sent via `payload_json`

**Implementation:**

- [x] `file_share_mcp/discord_upload.py` -- Discord REST API upload via `aiohttp` with retry logic
- [x] Implement `send_file` tool with channel validation, path validation, deny list
- [x] Implement `send_generated_file` tool with filename sanitization, temp file lifecycle
- [x] Add `file_sharing` section to `config.yaml`
- [x] Add `file-share` server to `discord-mcp.json` with `PYTHONUNBUFFERED=1`, `FILE_SHARE_CONFIG`, `FILE_SHARE_PROJECT_ROOT`
- [x] Add new MCP tool names to `ALLOWED_TOOLS` in `watcher.py`
- [x] Update system prompt: add `## File Sharing` section between `## Memory` and `## Security`
- [x] Add dynamic file manifest generation to `build_system_prompt()`
- [x] Update `requirements.txt` with `mcp` and `aiohttp`

### Phase 3: Polish & Verification

- [ ] Live test: request a file by name in Discord
- [ ] Live test: request a nonexistent file
- [ ] Live test: attempt path traversal via Discord message
- [ ] Live test: attempt cross-channel file delivery
- [ ] Live test: share a generated summary
- [ ] Live test: verify proactive sharing works naturally
- [ ] Verify `ATTACH_FILES` permission is set on the bot
- [ ] Verify temp file cleanup under crash conditions (kill MCP server, restart, check cleanup)
- [ ] Update LaunchAgent plist if Python path changed
- [ ] Update `scripts/install.sh` to include `mcp` and `aiohttp` deps

## Security Model

**Explicit trust model**: All users in monitored channels are trusted to access all files in allowed paths. There is no per-user or per-role access control. If a file is in an allowed path and not on the deny list, any user who can post in a monitored channel can request it.

**Security boundary clarification**: The security model prevents **sharing** files to Discord, not **reading** files. Claude has unrestricted read access to the filesystem via built-in tools (`Read`, `Write`, `Edit`). The file-sharing security layer (allowed paths, deny list, channel validation) controls what gets **uploaded to Discord as an attachment**, not what Claude can see.

**Defense layers** (in order of evaluation):
1. Channel-ID validation (prevents cross-server exfiltration)
2. Path validation with directory boundary check (prevents traversal)
3. Symlink rejection (prevents link-based escapes)
4. Deny list with fnmatch patterns (prevents sensitive file sharing)
5. File size limit (prevents oversized uploads)
6. Rate limiter (bounds blast radius of bulk exfiltration attempts)
7. System prompt guidance (soft control: max 3 files per request)

## v1 Scope Limitations (Documented, Not Deferred Bugs)

- **No PDF generation** -- generated content is plain text/Markdown only. PDF generation (via weasyprint/pandoc) deferred to v2.
- **No role-based authorization** -- any user in a monitored channel can request files. Mitigated by channel validation and rate limiting. Document as a security consideration for server admins.
- **No multi-file sharing** -- one file per tool call. Claude can make multiple sequential calls (up to 3 per request per system prompt guidance).
- **No boost-aware size limits** -- hardcoded to `max_file_size_mb` config value (default 10MB). Dynamic boost detection deferred.
- **No DM file sharing** -- consistent with existing DM limitation.
- **No image/diagram generation** -- only existing files and text-based generated content.
- **No inbound file handling** -- the bot cannot receive/process files users upload to Discord. When file sharing exists, users will naturally expect this. Deferred to v2.
- **No message deletion** -- if the bot shares the wrong file, it cannot delete the message. Deferred to v2 (requires extending `mcp-discord` or adding `delete_message` to file-share server).
- **No content search** -- Claude cannot search file contents to find "the document about X." It can only browse by filename/glob. Deferred to v2 (would require a `search_file_contents` tool).
- **Symlinks rejected outright** -- no symlink traversal support. Simplifies security model for v1.

## Sources & References

### Internal References

- Existing bot plan: `docs/plans/2026-03-09-feat-discord-claude-bot-plan.md`
- Watcher implementation: `watcher.py` (ALLOWED_TOOLS at line 241, build_system_prompt, ClaudeProcess)
- MCP config: `discord-mcp.json`
- Bot config: `config.yaml`
- Test patterns: `tests/test_watcher.py` (unittest.TestCase style, bare asserts, in-method imports)

### External References

- discord.py File uploads: https://discordpy.readthedocs.io/en/latest/faq.html
- Discord REST API (Create Message): https://discord.com/developers/docs/resources/message#create-message
- Discord Rate Limits: https://docs.discord.com/developers/topics/rate-limits
- Discord file size limits: 10MB default, higher with server boosts
- MCP Python SDK (FastMCP): https://github.com/modelcontextprotocol/python-sdk
- MCP Python SDK on PyPI: https://pypi.org/project/mcp/ (v1.26.0, requires Python >= 3.10)
- mcp-discord (barryyip0625): https://github.com/barryyip0625/mcp-discord -- confirmed no file attachment support
- CVE-2025-53109 (Anthropic filesystem MCP path validation bypass): https://embracethered.com/blog/posts/2025/anthropic-filesystem-mcp-server-bypass/
- MCP Security Best Practices: https://modelcontextprotocol.io/specification/draft/basic/security_best_practices
- MCP Tool Design Patterns: https://www.philschmid.de/mcp-best-practices
- MCP Tool Patterns (Arcade): https://www.arcade.dev/blog/mcp-tool-patterns
- Python tempfile security: https://security.openstack.org/guidelines/dg_using-temporary-files-securely.html
- Reference MCP filesystem server: https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem
