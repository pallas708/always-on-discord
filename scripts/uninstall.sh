#!/bin/bash
launchctl unload ~/Library/LaunchAgents/com.pallas.discord-claude-bot.plist 2>/dev/null
rm -f ~/Library/LaunchAgents/com.pallas.discord-claude-bot.plist
echo "Bot stopped and uninstalled."
