# 2026-03-09: Initial Implementation

All four phases of the Discord Claude bot built in a single pass. 22 unit tests passing.

## What was built

**watcher.py** — Full bot implementation:
- Discord websocket connection via discord.py
- Persistent Claude CLI subprocess with stream-json I/O
- Message guards (skip bots, self, system messages, empty, unconfigured channels)
- Message formatting as stream-json for Claude stdin
- Context rotation (200 messages or 6 hours)
- Crash recovery with exponential backoff (1s→30s cap, stops after 5 restarts in 5min)
- Bounded message buffer (20) for Claude downtime
- Memory backup on startup, size monitoring (500KB limit)
- Graceful shutdown on SIGTERM/SIGINT
- Structured logging to stderr

**Config & supporting files:**
- `config.yaml` — channel IDs, Claude CLI path, logging level
- `discord-mcp.json` — MCP server config (npx mcp-discord)
- `persona.md` — placeholder personality
- `memory.json` — initial empty store
- `.env` — bot token (chmod 600)
- `requirements.txt` — discord.py, pyyaml, python-dotenv

**Daemon:**
- `com.pallas.discord-claude-bot.plist` — macOS LaunchAgent (auto-start, keep-alive)
- `scripts/install.sh` — venv setup + LaunchAgent install
- `scripts/uninstall.sh` — LaunchAgent removal

**Tests** — 22 unit tests covering:
- Config loading
- System prompt construction (persona, core instructions, security warning)
- Message formatting (stream-json schema, mentions, single-line JSON)
- Message guards (6 filter conditions)
- Message buffer (bounded size, drain)
- Context rotation (count-based, reset)
- Restart backoff (exponential, cap, stop threshold)

## Verification results

**stream-json format:**
- Input: `{"type":"user","message":{"role":"user","content":"..."}}`
- Output: NDJSON — `system` (init), `assistant`, `rate_limit_event`, `result`
- Requires `--verbose` flag for stream-json output
- Must unset `CLAUDECODE` env var to avoid nested session error

**MCP tool names confirmed:**
- `mcp__discord__discord_send`
- `mcp__discord__discord_read_messages`
- `mcp__discord__discord_get_server_info`
- `discord_search_messages` does NOT exist (plan was wrong)
- `--strict-mcp-config` avoids loading user's other MCP servers

**Claude CLI flags used:**
```
claude --print --verbose
  --input-format stream-json --output-format stream-json
  --mcp-config discord-mcp.json --strict-mcp-config
  --system-prompt <persona + instructions>
  --permission-mode bypassPermissions
  --max-turns 10
  --allowedTools <scoped list>
```

## Live test findings

**Stdout buffering fix:** Claude CLI buffers its stdout when connected to a pipe. The init message only flushes when the first stdin write occurs. Fix: send a startup ping immediately after spawning the process.

**Deferred MCP tools:** Discord MCP tools are "deferred" in Claude CLI v2.1.71. Claude needs `ToolSearch` in `--allowedTools` to discover them at runtime. Also added `mcp__discord__discord_login` which Claude calls to authenticate with Discord.

**End-to-end verified:** Bot received message from user (silenus) in #general, Claude called `discord_send` to respond. Full flow working.
