# Always-On Discord Bot

A Discord bot that pipes messages through Claude Code CLI so Claude can autonomously decide when and how to respond.

```
Discord                              Discord
  │                                    ▲
  │ websocket                          │ MCP tools
  ▼                                    │
watcher.py ──stdin/stdout──▶ claude CLI ──▶ discord MCP server
               stream-json      │
                                ▼
                            persona.md
                            memory.json
```

## Prerequisites

- Python 3.9.6+
- Node.js / npm (for the Discord MCP server)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed at `~/.local/bin/claude`
- A Discord bot token with Message Content intent enabled

## Setup

1. Clone the repo and `cd` into it.

2. Create `.env` with your bot token:
   ```
   DISCORD_BOT_TOKEN=your-token-here
   ```

3. Edit `config.yaml` — set your channel IDs and paths.

4. Edit `persona.md` — define the bot's personality.

5. Install and start:
   ```
   bash scripts/install.sh
   ```
   This creates a `.venv`, installs deps from `requirements.txt`, and loads a macOS LaunchAgent that keeps the bot running.

## Run

**Manual** (foreground, for debugging):
```
source .venv/bin/activate
python watcher.py
```

**Daemon** (installed by `install.sh`):
```
# already running via LaunchAgent — check with:
launchctl list | grep discord-claude
```

## Logs

```
tail -f logs/bot.err
```

Stdout goes to `logs/bot.log` but most output is on stderr.

## Tests

```
python -m pytest tests/ -v
```

## Uninstall

```
bash scripts/uninstall.sh
```

Stops the LaunchAgent and removes it from `~/Library/LaunchAgents/`.
