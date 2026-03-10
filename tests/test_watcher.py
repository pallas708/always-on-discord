"""Tests for watcher.py — Discord event bridge to Claude CLI."""

import asyncio
import json
import os
import signal
import unittest
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import yaml


# We'll import from watcher once it exists. For now, define expected behavior.
# Tests are written first per CLAUDE.md workflow.


class TestConfig(unittest.TestCase):
    """Test config loading from config.yaml."""

    def test_load_config(self):
        from watcher import load_config
        with patch("builtins.open", mock_open(read_data=yaml.dump({
            "channels": [123456],
            "claude": {"path": "/usr/bin/claude", "max_turns": 10},
            "paths": {"persona": "persona.md", "memory": "memory.json", "mcp_config": "discord-mcp.json"},
            "logging": {"level": "INFO"},
        }))):
            cfg = load_config("config.yaml")
        assert cfg["channels"] == [123456]
        assert cfg["claude"]["path"] == "/usr/bin/claude"
        assert cfg["claude"]["max_turns"] == 10

    def test_load_config_missing_file(self):
        from watcher import load_config
        with self.assertRaises(FileNotFoundError):
            load_config("/nonexistent/config.yaml")


class TestSystemPrompt(unittest.TestCase):
    """Test system prompt construction from persona.md + core instructions."""

    def test_build_system_prompt_includes_persona(self):
        from watcher import build_system_prompt
        persona_text = "I am a friendly bot."
        prompt = build_system_prompt(persona_text)
        assert "I am a friendly bot." in prompt

    def test_build_system_prompt_includes_core_instructions(self):
        from watcher import build_system_prompt
        prompt = build_system_prompt("Test persona")
        assert "discord_send" in prompt
        assert "memory.json" in prompt
        assert "@mentioned" in prompt.lower() or "mention" in prompt.lower()

    def test_build_system_prompt_includes_security_warning(self):
        from watcher import build_system_prompt
        prompt = build_system_prompt("Test persona")
        assert "untrusted" in prompt.lower() or "security" in prompt.lower()


class TestMessageFormatting(unittest.TestCase):
    """Test formatting Discord messages as stream-json for Claude stdin."""

    def test_format_message_basic(self):
        from watcher import format_message_for_claude
        result = format_message_for_claude(
            channel_name="general",
            channel_id=123456,
            author_name="Alice",
            content="Hello world",
            bot_mentioned=False,
        )
        data = json.loads(result)
        assert data["type"] == "user"
        assert data["message"]["role"] == "user"
        assert "general" in data["message"]["content"]
        assert "Alice" in data["message"]["content"]
        assert "Hello world" in data["message"]["content"]

    def test_format_message_with_mention(self):
        from watcher import format_message_for_claude
        result = format_message_for_claude(
            channel_name="general",
            channel_id=123456,
            author_name="Bob",
            content="Hey bot!",
            bot_mentioned=True,
        )
        data = json.loads(result)
        assert "true" in data["message"]["content"].lower() or "mentioned" in data["message"]["content"].lower()

    def test_format_message_is_valid_json_line(self):
        from watcher import format_message_for_claude
        result = format_message_for_claude(
            channel_name="test",
            channel_id=1,
            author_name="User",
            content="test",
            bot_mentioned=False,
        )
        # Must be a single line (no embedded newlines in the JSON)
        assert "\n" not in result


class TestMessageGuards(unittest.TestCase):
    """Test message filtering logic."""

    def test_skip_bot_messages(self):
        from watcher import should_process_message
        msg = MagicMock()
        msg.author.bot = True
        msg.type = MagicMock()
        msg.type.value = 0  # default message type
        msg.content = "hello"
        msg.channel.id = 123
        assert should_process_message(msg, bot_user_id=999, channels=[123]) is False

    def test_skip_own_messages(self):
        from watcher import should_process_message
        msg = MagicMock()
        msg.author.bot = False
        msg.author.id = 999
        msg.type = MagicMock()
        msg.type.value = 0
        msg.content = "hello"
        msg.channel.id = 123
        assert should_process_message(msg, bot_user_id=999, channels=[123]) is False

    def test_skip_non_default_message_type(self):
        from watcher import should_process_message
        msg = MagicMock()
        msg.author.bot = False
        msg.author.id = 100
        msg.type = MagicMock()
        msg.type.value = 7  # member join
        msg.content = ""
        msg.channel.id = 123
        assert should_process_message(msg, bot_user_id=999, channels=[123]) is False

    def test_skip_empty_content(self):
        from watcher import should_process_message
        msg = MagicMock()
        msg.author.bot = False
        msg.author.id = 100
        msg.type = MagicMock()
        msg.type.value = 0
        msg.content = ""
        msg.channel.id = 123
        assert should_process_message(msg, bot_user_id=999, channels=[123]) is False

    def test_skip_unconfigured_channel(self):
        from watcher import should_process_message
        msg = MagicMock()
        msg.author.bot = False
        msg.author.id = 100
        msg.type = MagicMock()
        msg.type.value = 0
        msg.content = "hello"
        msg.channel.id = 999999
        assert should_process_message(msg, bot_user_id=999, channels=[123]) is False

    def test_accept_valid_message(self):
        from watcher import should_process_message
        msg = MagicMock()
        msg.author.bot = False
        msg.author.id = 100
        msg.type = MagicMock()
        msg.type.value = 0
        msg.content = "hello"
        msg.channel.id = 123
        assert should_process_message(msg, bot_user_id=999, channels=[123]) is True


class TestMessageBuffer(unittest.TestCase):
    """Test bounded message buffer for Claude downtime."""

    def test_buffer_bounded_size(self):
        from watcher import MessageBuffer
        buf = MessageBuffer(max_size=3)
        for i in range(5):
            buf.add(f"msg{i}")
        assert len(buf) == 3
        # Should keep the newest 3
        assert list(buf.drain()) == ["msg2", "msg3", "msg4"]

    def test_buffer_drain_empties(self):
        from watcher import MessageBuffer
        buf = MessageBuffer(max_size=10)
        buf.add("a")
        buf.add("b")
        items = list(buf.drain())
        assert items == ["a", "b"]
        assert len(buf) == 0


class TestContextRotation(unittest.TestCase):
    """Test context rotation tracking."""

    def test_rotation_needed_by_count(self):
        from watcher import ContextTracker
        tracker = ContextTracker(max_messages=5, max_hours=6)
        for _ in range(5):
            tracker.record_message()
        assert tracker.needs_rotation() is True

    def test_rotation_not_needed(self):
        from watcher import ContextTracker
        tracker = ContextTracker(max_messages=200, max_hours=6)
        for _ in range(10):
            tracker.record_message()
        assert tracker.needs_rotation() is False

    def test_rotation_resets(self):
        from watcher import ContextTracker
        tracker = ContextTracker(max_messages=5, max_hours=6)
        for _ in range(5):
            tracker.record_message()
        assert tracker.needs_rotation() is True
        tracker.reset()
        assert tracker.needs_rotation() is False


class TestRestartBackoff(unittest.TestCase):
    """Test exponential backoff for process restarts."""

    def test_backoff_increases(self):
        from watcher import RestartTracker
        tracker = RestartTracker(max_restarts=5, window_seconds=300)
        assert tracker.get_backoff() == 1
        tracker.record_restart()
        assert tracker.get_backoff() == 2
        tracker.record_restart()
        assert tracker.get_backoff() == 4

    def test_backoff_capped(self):
        from watcher import RestartTracker
        tracker = RestartTracker(max_restarts=5, window_seconds=300)
        for _ in range(10):
            tracker.record_restart()
        assert tracker.get_backoff() <= 30

    def test_too_many_restarts(self):
        from watcher import RestartTracker
        tracker = RestartTracker(max_restarts=5, window_seconds=300)
        for _ in range(6):
            tracker.record_restart()
        assert tracker.should_stop() is True


if __name__ == "__main__":
    unittest.main()
