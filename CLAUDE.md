# Always-On Discord Bot

Discord bot powered by Claude Code CLI with MCP integration.

## Development Workflow

**Test-first development.** Write tests before implementation for every functional block:

1. Write a failing test that defines the expected behavior
2. Implement the minimum code to make it pass
3. Refactor if needed, re-run tests to confirm

Run tests with: `python -m pytest tests/ -v`

Do not consider a feature complete until its tests pass. If a test can't be written first (e.g., integration with external services), write it immediately after and note the reason.

## Project Structure

- `watcher.py` — Thin Python process: Discord websocket → Claude stdin
- `persona.md` — Bot personality and engagement rules
- `memory.json` — Long-term context (read/written by Claude at runtime, not committed)
- `config.yaml` — Channel IDs, paths, settings
- `discord-mcp.json` — MCP server config for Discord tools
- `tests/` — Test suite (pytest)

## Key Conventions

- Python 3.9.6 (system), dependencies in `.venv`
- Secrets in `.env` (never committed, chmod 600)
- Plan doc: `docs/plans/2026-03-09-feat-discord-claude-bot-plan.md`
