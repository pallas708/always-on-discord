# Brainstorm: Discord Bot Powered by Claude Code

**Date:** 2026-03-09
**Status:** Draft

## What We're Building

A Discord bot that acts as a natural chat participant across multiple channels, powered by Claude Code running on a Max subscription. The system uses a **hybrid architecture**: a persistent Python process watches Discord via websocket for real-time message detection, then invokes the `claude` CLI as a subprocess to generate intelligent, selective responses.

The bot has a **custom persona** (configurable via file) and uses Claude's judgment to decide which messages warrant a response — acting like a thoughtful participant rather than a command-response bot.

### Core Behavior

- **Always-on listener** via persistent Python websocket connection to Discord
- **Selective responder** — Claude decides what's worth responding to
- **Multi-channel** — monitors several Discord channels simultaneously
- **Custom persona** — name and personality defined in a config file
- **Memory across sessions** — combines Discord message history with a local memory file for long-term context

## Why This Approach

### Hybrid: Python Watcher + Claude CLI Subprocess

Chosen over simpler polling (cron/loop) approaches because:

1. **Near real-time** — websocket connection means no 1-minute polling delay
2. **Efficient** — Claude is only invoked when there are messages to evaluate
3. **Simple invocation model** — each `claude` CLI call is independent, no session management complexity
4. **Max subscription** — uses existing subscription, no API costs
5. **Resilient** — Python watcher is simple enough to auto-restart; Claude CLI calls are stateless

### Alternatives Considered

- **Cron + Python scripts**: Simpler but 1-minute latency, unnecessary invocations on quiet channels
- **Loop + Python scripts**: Requires open terminal, less resilient than a daemon
- Both were rejected in favor of real-time websocket approach

## Architecture

```
Discord (Websocket) → Python Watcher (discord.py)
                          ↓ (on every message)
                    Claude CLI subprocess
                    (reads: message + channel history + memory file + persona config)
                          ↓
                    Decision: respond or not
                          ↓ (if responding)
                    Python posts response via Discord API
                          ↓
                    Updates local memory file
```

### Components

1. **`watcher.py`** — Persistent Python script using `discord.py` gateway
   - Connects to Discord via websocket (bot token)
   - Listens for messages in configured channels
   - On each message: invokes `claude` CLI with context
   - Posts Claude's response back to Discord
   - Handles reconnection, error recovery

2. **`persona.md`** — Custom persona configuration
   - Name, personality traits, communication style
   - Topics to engage with vs. ignore
   - Tone and behavior guidelines
   - Editable by user at any time

3. **`memory.json`** — Local memory file
   - Tracks ongoing conversation threads
   - Stores key facts about users and topics
   - Updated after each Claude invocation
   - Provides continuity across independent CLI calls

4. **`config.yaml`** — Bot configuration
   - Discord bot token
   - Channel IDs to monitor
   - Path to persona file
   - Memory file path
   - Any rate-limiting settings

5. **LaunchAgent plist** — `com.pallas.discord-claude-bot.plist` for launchd
   - Auto-start on login, auto-restart on crash
   - Logs to a known location for debugging

### Edge Cases

- **Self-response loop** — The bot must ignore its own messages. When `watcher.py` receives a message, check if `message.author == bot.user` and skip immediately. Without this, the bot could trigger itself infinitely.
- **Bot messages from others** — Also ignore messages from other bots to avoid bot-to-bot loops.

### Message Flow (Detail)

1. User posts message in monitored channel
2. `watcher.py` receives message via websocket
3. **Guard:** If message is from the bot itself or another bot, skip
4. Watcher fetches last ~20 messages from that channel for context
5. Watcher reads `memory.json` for long-term context
6. Watcher writes a temporary prompt file containing: persona, recent messages, memory context, and the instruction to respond or output `SKIP`
7. Watcher invokes: `claude --print --prompt-file /tmp/discord_prompt.txt`
8. If Claude outputs a response (not `SKIP`), watcher posts it to Discord
9. Watcher appends to `memory.json` with new context

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Architecture | Hybrid (Python watcher + Claude CLI) | Real-time, efficient, simple |
| Pre-filter | None — all messages forwarded to Claude | Let Claude's judgment drive selectivity |
| Claude invocation | `claude` CLI subprocess per message | Stateless, simple, reliable |
| Memory strategy | Discord history + local memory file | Best of both worlds |
| Persona | Configurable via file | Flexible, easy to iterate |
| Channel scope | Multiple channels | Broader presence |
| Response style | Selective — Claude decides | Natural participant feel |
| Concurrency | Parallel (up to N) | Fast response in busy channels |
| Process management | macOS launchd | Auto-start, auto-restart, robust |
| Bot permissions | Full | Maximum flexibility for natural participation |

## Resolved Questions

1. **Concurrency** — Allow parallel Claude CLI processes (up to N concurrent, starting at N=3). Multiple instances can run simultaneously for fast response in busy channels.
2. **Process management** — Use macOS **launchd** (LaunchAgent). Auto-starts on login, auto-restarts on crash.
3. **Bot permissions** — Full permissions: read/send messages, read history, embed links, add reactions, attach files, manage threads, use slash commands.

## Open Questions

1. **Rate limiting** — Max subscription's effective rate limit for CLI invocations is unknown. Start conservative (N=3 parallel) and adjust empirically.
2. **Memory concurrency** — With parallel Claude processes, concurrent writes to `memory.json` risk data corruption. Needs a locking strategy (file lock, append-only log, or per-channel memory files).
3. **Python version** — System Python is 3.9.6. Verify `discord.py` compatibility or plan to install a newer Python via Homebrew.
