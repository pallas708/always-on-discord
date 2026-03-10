#!/bin/bash
set -e
cd "$(dirname "$0")/.."

echo "Setting up Discord Claude bot..."

# Python venv
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Ensure logs directory exists
mkdir -p logs

# Install LaunchAgent
cp com.pallas.discord-claude-bot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pallas.discord-claude-bot.plist

echo "Bot installed and started."
echo "Check logs: tail -f logs/bot.err"
