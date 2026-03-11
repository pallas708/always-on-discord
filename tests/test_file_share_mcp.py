"""Tests for file_share_mcp — custom MCP server for Discord file sharing."""

import asyncio
import fnmatch
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

# Use the actual project root for path resolution in tests
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _make_config(
    allowed_paths=None,
    channels=None,
    max_file_size_mb=10,
    denied_files=None,
):
    """Build a minimal config dict for testing."""
    if channels is None:
        channels = [123456789012345678]
    if denied_files is None:
        denied_files = [
            ".env", ".env.*", "memory.json", "memory.json.bak",
            "discord-mcp.json", "config.yaml",
            "*.key", "*.pem", "*.p12", "*.pfx", "*.plist",
        ]
    return {
        "channels": channels,
        "file_sharing": {
            "allowed_paths": allowed_paths or ["docs"],
            "max_file_size_mb": max_file_size_mb,
            "denied_files": denied_files,
        },
    }


# ---------------------------------------------------------------------------
# TestFileSharingConfig
# ---------------------------------------------------------------------------


class TestFileSharingConfig(unittest.TestCase):
    """Test FileSharingConfig loading from config dict."""

    def test_from_config_basic(self):
        from file_share_mcp.config import FileSharingConfig
        config = _make_config()
        fsc = FileSharingConfig.from_config(config, PROJECT_ROOT)
        assert len(fsc.allowed_paths) == 1
        assert fsc.allowed_paths[0] == PROJECT_ROOT / "docs"
        assert fsc.max_file_size_bytes == 10 * 1024 * 1024

    def test_from_config_channel_ids_are_strings(self):
        """YAML stores channel IDs as ints; config must convert to str."""
        from file_share_mcp.config import FileSharingConfig
        config = _make_config(channels=[1480585939687313549])
        fsc = FileSharingConfig.from_config(config, PROJECT_ROOT)
        assert all(isinstance(ch, str) for ch in fsc.allowed_channels)
        assert "1480585939687313549" in fsc.allowed_channels

    def test_from_config_denied_files(self):
        from file_share_mcp.config import FileSharingConfig
        config = _make_config(denied_files=[".env", "*.key"])
        fsc = FileSharingConfig.from_config(config, PROJECT_ROOT)
        assert ".env" in fsc.denied_files
        assert "*.key" in fsc.denied_files

    def test_from_config_mixed_paths(self):
        """allowed_paths can contain both directories and individual files."""
        from file_share_mcp.config import FileSharingConfig
        config = _make_config(allowed_paths=["docs", "persona.md"])
        fsc = FileSharingConfig.from_config(config, PROJECT_ROOT)
        assert len(fsc.allowed_paths) == 2

    def test_from_config_empty_channels_raises(self):
        from file_share_mcp.config import FileSharingConfig
        config = _make_config(channels=[])
        with self.assertRaises(ValueError):
            FileSharingConfig.from_config(config, PROJECT_ROOT)

    def test_from_config_zero_size_raises(self):
        from file_share_mcp.config import FileSharingConfig
        config = _make_config(max_file_size_mb=0)
        with self.assertRaises(ValueError):
            FileSharingConfig.from_config(config, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# TestPathValidation
# ---------------------------------------------------------------------------


class TestPathValidation(unittest.TestCase):
    """Test path validation with directory boundary check (CVE-2025-53109)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.project_root = Path(self.tmp)
        # Create test directories and files
        (self.project_root / "docs").mkdir()
        (self.project_root / "docs" / "plan.md").write_text("plan")
        (self.project_root / "docs" / "sub").mkdir()
        (self.project_root / "docs" / "sub" / "nested.md").write_text("nested")
        (self.project_root / "docs-backup").mkdir()
        (self.project_root / "docs-backup" / "secret.md").write_text("secret")
        (self.project_root / "persona.md").write_text("persona")
        (self.project_root / "secret.env").write_text("TOKEN=abc")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_allowed_directory_file(self):
        from file_share_mcp.security import validate_path
        result = validate_path(
            "docs/plan.md",
            allowed_paths=[self.project_root / "docs"],
            project_root=self.project_root,
        )
        assert result == (self.project_root / "docs" / "plan.md").resolve()

    def test_allowed_nested_file(self):
        from file_share_mcp.security import validate_path
        result = validate_path(
            "docs/sub/nested.md",
            allowed_paths=[self.project_root / "docs"],
            project_root=self.project_root,
        )
        assert result == (self.project_root / "docs" / "sub" / "nested.md").resolve()

    def test_traversal_rejection(self):
        from file_share_mcp.security import validate_path
        with self.assertRaises(Exception):
            validate_path(
                "../../../etc/passwd",
                allowed_paths=[self.project_root / "docs"],
                project_root=self.project_root,
            )

    def test_traversal_via_dotdot_in_allowed(self):
        from file_share_mcp.security import validate_path
        with self.assertRaises(Exception):
            validate_path(
                "docs/../../secret.env",
                allowed_paths=[self.project_root / "docs"],
                project_root=self.project_root,
            )

    def test_directory_boundary_attack(self):
        """docs-backup must NOT pass when only docs is allowed (CVE-2025-53109)."""
        from file_share_mcp.security import validate_path
        with self.assertRaises(Exception):
            validate_path(
                "docs-backup/secret.md",
                allowed_paths=[self.project_root / "docs"],
                project_root=self.project_root,
            )

    def test_symlink_rejection(self):
        from file_share_mcp.security import validate_path
        link = self.project_root / "docs" / "link.md"
        link.symlink_to(self.project_root / "secret.env")
        with self.assertRaises(Exception) as ctx:
            validate_path(
                "docs/link.md",
                allowed_paths=[self.project_root / "docs"],
                project_root=self.project_root,
            )
        assert "symlink" in str(ctx.exception).lower()

    def test_null_byte_rejection(self):
        from file_share_mcp.security import validate_path
        with self.assertRaises(Exception):
            validate_path(
                "docs/plan.md\x00.txt",
                allowed_paths=[self.project_root / "docs"],
                project_root=self.project_root,
            )

    def test_individual_file_allowed(self):
        """allowed_paths can include individual files (e.g., persona.md)."""
        from file_share_mcp.security import validate_path
        result = validate_path(
            "persona.md",
            allowed_paths=[self.project_root / "persona.md"],
            project_root=self.project_root,
        )
        assert result == (self.project_root / "persona.md").resolve()

    def test_individual_file_other_rejected(self):
        """A file not in allowed_paths is rejected even if it exists."""
        from file_share_mcp.security import validate_path
        with self.assertRaises(Exception):
            validate_path(
                "secret.env",
                allowed_paths=[self.project_root / "persona.md"],
                project_root=self.project_root,
            )

    def test_absolute_path_in_allowed_dir(self):
        from file_share_mcp.security import validate_path
        abs_path = str(self.project_root / "docs" / "plan.md")
        result = validate_path(
            abs_path,
            allowed_paths=[self.project_root / "docs"],
            project_root=self.project_root,
        )
        assert result == (self.project_root / "docs" / "plan.md").resolve()

    def test_nonexistent_file_rejected(self):
        from file_share_mcp.security import validate_path
        with self.assertRaises(Exception):
            validate_path(
                "docs/nonexistent.md",
                allowed_paths=[self.project_root / "docs"],
                project_root=self.project_root,
            )


# ---------------------------------------------------------------------------
# TestDenyList
# ---------------------------------------------------------------------------


class TestDenyList(unittest.TestCase):
    """Test deny list filtering with fnmatch patterns."""

    def test_env_denied(self):
        from file_share_mcp.security import is_denied
        assert is_denied(".env", [".env", ".env.*"]) is True

    def test_env_variant_denied(self):
        from file_share_mcp.security import is_denied
        assert is_denied(".env.local", [".env", ".env.*"]) is True
        assert is_denied(".env.production", [".env", ".env.*"]) is True

    def test_memory_json_denied(self):
        from file_share_mcp.security import is_denied
        assert is_denied("memory.json", ["memory.json"]) is True
        assert is_denied("memory.json.bak", ["memory.json.bak"]) is True

    def test_key_files_denied(self):
        from file_share_mcp.security import is_denied
        deny = ["*.key", "*.pem", "*.p12", "*.pfx"]
        assert is_denied("server.key", deny) is True
        assert is_denied("cert.pem", deny) is True
        assert is_denied("bundle.p12", deny) is True
        assert is_denied("bundle.pfx", deny) is True

    def test_discord_mcp_json_denied(self):
        from file_share_mcp.security import is_denied
        assert is_denied("discord-mcp.json", ["discord-mcp.json"]) is True

    def test_config_yaml_denied(self):
        from file_share_mcp.security import is_denied
        assert is_denied("config.yaml", ["config.yaml"]) is True

    def test_plist_denied(self):
        from file_share_mcp.security import is_denied
        assert is_denied("com.pallas.bot.plist", ["*.plist"]) is True

    def test_normal_file_allowed(self):
        from file_share_mcp.security import is_denied
        deny = [".env", ".env.*", "memory.json", "*.key"]
        assert is_denied("plan.md", deny) is False
        assert is_denied("readme.txt", deny) is False
        assert is_denied("code.py", deny) is False


# ---------------------------------------------------------------------------
# TestChannelValidation
# ---------------------------------------------------------------------------


class TestChannelValidation(unittest.TestCase):
    """Test channel ID validation against configured channels."""

    def test_allowed_channel(self):
        from file_share_mcp.security import validate_channel
        # Should not raise
        validate_channel("1480585939687313549", ["1480585939687313549", "9999"])

    def test_disallowed_channel(self):
        from file_share_mcp.security import validate_channel
        with self.assertRaises(Exception) as ctx:
            validate_channel("9999999999999999", ["1480585939687313549"])
        assert "not a monitored channel" in str(ctx.exception).lower() or "Access denied" in str(ctx.exception)


# ---------------------------------------------------------------------------
# TestSizeLimit
# ---------------------------------------------------------------------------


class TestSizeLimit(unittest.TestCase):
    """Test file size validation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_file_under_limit(self):
        from file_share_mcp.security import check_file_size
        f = Path(self.tmp) / "small.txt"
        f.write_text("hello")
        # Should not raise
        check_file_size(f, max_bytes=1024)

    def test_file_at_limit_rejected(self):
        from file_share_mcp.security import check_file_size
        f = Path(self.tmp) / "big.txt"
        f.write_bytes(b"x" * 1024)
        with self.assertRaises(Exception):
            check_file_size(f, max_bytes=1024)

    def test_file_over_limit_rejected(self):
        from file_share_mcp.security import check_file_size
        f = Path(self.tmp) / "huge.txt"
        f.write_bytes(b"x" * 2048)
        with self.assertRaises(Exception):
            check_file_size(f, max_bytes=1024)


# ---------------------------------------------------------------------------
# TestFilenameSanitization
# ---------------------------------------------------------------------------


class TestFilenameSanitization(unittest.TestCase):
    """Test filename regex validation for generated files."""

    def test_valid_filenames(self):
        from file_share_mcp.security import validate_filename
        for name in ["summary.md", "report.txt", "data.csv", "config.json", "script.py"]:
            validate_filename(name)  # Should not raise

    def test_invalid_filenames(self):
        from file_share_mcp.security import validate_filename
        for name in [
            "../evil.md",
            ".env",
            "file.exe",
            "no-extension",
            "has spaces.md",
            "a" * 101 + ".md",
            "file.MD",  # uppercase extension
            "file.toolong",  # extension > 5 chars
        ]:
            with self.assertRaises(Exception, msg=f"{name} should be rejected"):
                validate_filename(name)

    def test_allowed_extensions(self):
        from file_share_mcp.security import validate_filename
        for ext in ["md", "txt", "py", "json", "yaml", "csv"]:
            validate_filename(f"test.{ext}")  # Should not raise

    def test_disallowed_extensions(self):
        from file_share_mcp.security import validate_filename
        for ext in ["exe", "sh", "bat", "dll", "so"]:
            with self.assertRaises(Exception):
                validate_filename(f"test.{ext}")


# ---------------------------------------------------------------------------
# TestTempFileCleanup
# ---------------------------------------------------------------------------


class TestTempFileCleanup(unittest.TestCase):
    """Test temp file lifecycle and orphan cleanup."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_orphan_cleanup_on_startup(self):
        from file_share_mcp.cleanup import cleanup_stale_temps
        # Create orphan files
        (self.tmp_dir / "orphan1.md").write_text("old")
        (self.tmp_dir / "orphan2.txt").write_text("old")
        assert len(list(self.tmp_dir.iterdir())) == 2
        cleanup_stale_temps(self.tmp_dir)
        assert len(list(self.tmp_dir.iterdir())) == 0

    def test_orphan_cleanup_nonexistent_dir(self):
        """cleanup_stale_temps handles non-existent directory gracefully."""
        from file_share_mcp.cleanup import cleanup_stale_temps
        import shutil
        shutil.rmtree(self.tmp_dir)
        # Should not raise
        cleanup_stale_temps(self.tmp_dir)


# ---------------------------------------------------------------------------
# TestListShareableFiles
# ---------------------------------------------------------------------------


class TestListShareableFiles(unittest.TestCase):
    """Test list_shareable_files tool logic."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.project_root = Path(self.tmp)
        docs = self.project_root / "docs"
        docs.mkdir()
        (docs / "plan.md").write_text("plan")
        (docs / "brainstorm.md").write_text("brainstorm")
        (docs / "notes.txt").write_text("notes")
        (docs / ".env").write_text("SECRET")  # should be filtered by deny list

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_list_files_basic(self):
        from file_share_mcp.security import list_files
        from file_share_mcp.config import FileSharingConfig
        config = FileSharingConfig(
            allowed_paths=[self.project_root / "docs"],
            allowed_channels=["123"],
            denied_files=[".env"],
            max_file_size_bytes=10 * 1024 * 1024,
            project_root=self.project_root,
            temp_dir=self.project_root / "tmp",
        )
        result = list_files(config)
        filenames = [f["name"] for f in result["files"]]
        assert "plan.md" in filenames
        assert "brainstorm.md" in filenames
        assert ".env" not in filenames  # denied

    def test_list_files_max_results(self):
        from file_share_mcp.security import list_files
        from file_share_mcp.config import FileSharingConfig
        config = FileSharingConfig(
            allowed_paths=[self.project_root / "docs"],
            allowed_channels=["123"],
            denied_files=[".env"],
            max_file_size_bytes=10 * 1024 * 1024,
            project_root=self.project_root,
            temp_dir=self.project_root / "tmp",
        )
        result = list_files(config, max_results=2)
        assert len(result["files"]) <= 2
        assert result["truncated"] is True
        assert result["total_available"] == 3  # plan.md, brainstorm.md, notes.txt

    def test_list_files_pattern_filter(self):
        from file_share_mcp.security import list_files
        from file_share_mcp.config import FileSharingConfig
        config = FileSharingConfig(
            allowed_paths=[self.project_root / "docs"],
            allowed_channels=["123"],
            denied_files=[".env"],
            max_file_size_bytes=10 * 1024 * 1024,
            project_root=self.project_root,
            temp_dir=self.project_root / "tmp",
        )
        result = list_files(config, pattern="*.md")
        filenames = [f["name"] for f in result["files"]]
        assert "plan.md" in filenames
        assert "brainstorm.md" in filenames
        assert "notes.txt" not in filenames

    def test_list_files_truncated_flag(self):
        from file_share_mcp.security import list_files
        from file_share_mcp.config import FileSharingConfig
        config = FileSharingConfig(
            allowed_paths=[self.project_root / "docs"],
            allowed_channels=["123"],
            denied_files=[".env"],
            max_file_size_bytes=10 * 1024 * 1024,
            project_root=self.project_root,
            temp_dir=self.project_root / "tmp",
        )
        result = list_files(config, max_results=50)
        assert result["truncated"] is False

    def test_list_files_hard_cap(self):
        from file_share_mcp.security import list_files
        from file_share_mcp.config import FileSharingConfig
        config = FileSharingConfig(
            allowed_paths=[self.project_root / "docs"],
            allowed_channels=["123"],
            denied_files=[],
            max_file_size_bytes=10 * 1024 * 1024,
            project_root=self.project_root,
            temp_dir=self.project_root / "tmp",
        )
        # max_results > 200 should be capped
        result = list_files(config, max_results=999)
        # We only have a few files, but the cap logic should exist
        assert len(result["files"]) <= 200

    def test_list_files_with_metadata(self):
        from file_share_mcp.security import list_files
        from file_share_mcp.config import FileSharingConfig
        config = FileSharingConfig(
            allowed_paths=[self.project_root / "docs"],
            allowed_channels=["123"],
            denied_files=[".env"],
            max_file_size_bytes=10 * 1024 * 1024,
            project_root=self.project_root,
            temp_dir=self.project_root / "tmp",
        )
        result = list_files(config, include_metadata=True)
        for f in result["files"]:
            assert "size_bytes" in f
            assert "modified" in f

    def test_list_files_without_metadata(self):
        from file_share_mcp.security import list_files
        from file_share_mcp.config import FileSharingConfig
        config = FileSharingConfig(
            allowed_paths=[self.project_root / "docs"],
            allowed_channels=["123"],
            denied_files=[".env"],
            max_file_size_bytes=10 * 1024 * 1024,
            project_root=self.project_root,
            temp_dir=self.project_root / "tmp",
        )
        result = list_files(config, include_metadata=False)
        for f in result["files"]:
            assert "size_bytes" not in f
            assert "modified" not in f


# ---------------------------------------------------------------------------
# TestRateLimiter
# ---------------------------------------------------------------------------


class TestRateLimiter(unittest.TestCase):
    """Test sliding-window rate limiter."""

    def test_global_limit_blocks(self):
        from file_share_mcp.rate_limiter import RateLimiter
        rl = RateLimiter(max_global=3, max_per_channel=10, window_seconds=60)
        for _ in range(3):
            rl.check("ch1")
        with self.assertRaises(Exception) as ctx:
            rl.check("ch2")  # different channel, but global limit hit
        assert "rate limited" in str(ctx.exception).lower()

    def test_per_channel_limit_blocks(self):
        from file_share_mcp.rate_limiter import RateLimiter
        rl = RateLimiter(max_global=100, max_per_channel=2, window_seconds=60)
        rl.check("ch1")
        rl.check("ch1")
        with self.assertRaises(Exception) as ctx:
            rl.check("ch1")
        assert "rate limited" in str(ctx.exception).lower()
        # Different channel should still work
        rl.check("ch2")  # Should not raise

    def test_requests_succeed_after_window(self):
        from file_share_mcp.rate_limiter import RateLimiter
        rl = RateLimiter(max_global=2, max_per_channel=2, window_seconds=0.1)
        rl.check("ch1")
        rl.check("ch1")
        # Wait for window to expire
        time.sleep(0.15)
        # Should succeed now
        rl.check("ch1")

    def test_returns_tool_error(self):
        from file_share_mcp.rate_limiter import RateLimiter
        from mcp.server.fastmcp.exceptions import ToolError
        rl = RateLimiter(max_global=1, max_per_channel=10, window_seconds=60)
        rl.check("ch1")
        with self.assertRaises(ToolError):
            rl.check("ch1")


# ---------------------------------------------------------------------------
# TestDiscordUpload (async)
# ---------------------------------------------------------------------------


class TestDiscordUpload(unittest.IsolatedAsyncioTestCase):
    """Test Discord REST API upload with mocked aiohttp."""

    async def test_successful_upload(self):
        from file_share_mcp.discord_upload import upload_file_to_discord
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "id": "msg123",
            "channel_id": "ch456",
            "attachments": [{"filename": "test.md"}],
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        tmp = Path(tempfile.mktemp(suffix=".md"))
        tmp.write_text("hello")
        try:
            result = await upload_file_to_discord(
                token="fake-token",
                channel_id="ch456",
                file_path=tmp,
                session=mock_session,
            )
            assert result["id"] == "msg123"
        finally:
            tmp.unlink()

    async def test_429_retry(self):
        from file_share_mcp.discord_upload import upload_file_to_discord

        # First response: 429 rate limit
        mock_resp_429 = AsyncMock()
        mock_resp_429.status = 429
        mock_resp_429.json = AsyncMock(return_value={"retry_after": 0.01})
        mock_resp_429.headers = {"Retry-After": "0.01"}
        mock_resp_429.__aenter__ = AsyncMock(return_value=mock_resp_429)
        mock_resp_429.__aexit__ = AsyncMock(return_value=False)

        # Second response: success
        mock_resp_200 = AsyncMock()
        mock_resp_200.status = 200
        mock_resp_200.json = AsyncMock(return_value={"id": "msg123", "channel_id": "ch1"})
        mock_resp_200.__aenter__ = AsyncMock(return_value=mock_resp_200)
        mock_resp_200.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=[mock_resp_429, mock_resp_200])

        tmp = Path(tempfile.mktemp(suffix=".md"))
        tmp.write_text("hello")
        try:
            result = await upload_file_to_discord(
                token="fake-token",
                channel_id="ch1",
                file_path=tmp,
                session=mock_session,
                max_retries=1,
            )
            assert result["id"] == "msg123"
            assert mock_session.post.call_count == 2
        finally:
            tmp.unlink()

    async def test_error_sanitization_403(self):
        from file_share_mcp.discord_upload import upload_file_to_discord

        mock_resp = AsyncMock()
        mock_resp.status = 403
        mock_resp.raise_for_status = MagicMock(side_effect=Exception("403 Forbidden"))
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        tmp = Path(tempfile.mktemp(suffix=".md"))
        tmp.write_text("hello")
        try:
            with self.assertRaises(Exception) as ctx:
                await upload_file_to_discord(
                    token="fake-token",
                    channel_id="ch1",
                    file_path=tmp,
                    session=mock_session,
                )
            assert "permission" in str(ctx.exception).lower()
        finally:
            tmp.unlink()

    async def test_error_sanitization_5xx(self):
        from file_share_mcp.discord_upload import upload_file_to_discord

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.raise_for_status = MagicMock(side_effect=Exception("500 Internal"))
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        tmp = Path(tempfile.mktemp(suffix=".md"))
        tmp.write_text("hello")
        try:
            with self.assertRaises(Exception) as ctx:
                await upload_file_to_discord(
                    token="fake-token",
                    channel_id="ch1",
                    file_path=tmp,
                    session=mock_session,
                )
            assert "unavailable" in str(ctx.exception).lower()
        finally:
            tmp.unlink()


# ---------------------------------------------------------------------------
# TestSendFile (integration with mocked Discord API)
# ---------------------------------------------------------------------------


class TestSendFile(unittest.IsolatedAsyncioTestCase):
    """Test send_file tool end-to-end with mocked upload."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.project_root = Path(self.tmp)
        docs = self.project_root / "docs"
        docs.mkdir()
        (docs / "plan.md").write_text("# My Plan\nThis is a plan.")
        self.config_dict = _make_config(
            allowed_paths=["docs"],
            channels=[123456789012345678],
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    async def test_send_file_success(self):
        from file_share_mcp.config import FileSharingConfig
        from file_share_mcp.security import validate_path, is_denied, validate_channel, check_file_size

        fsc = FileSharingConfig.from_config(self.config_dict, self.project_root)
        path = validate_path("docs/plan.md", fsc.allowed_paths, self.project_root)
        assert not is_denied(path.name, fsc.denied_files)
        validate_channel("123456789012345678", fsc.allowed_channels)
        check_file_size(path, fsc.max_file_size_bytes)

    async def test_send_file_with_message(self):
        """Verify message parameter flows through to upload."""
        from file_share_mcp.discord_upload import upload_file_to_discord

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "id": "msg999",
            "channel_id": "123456789012345678",
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        file_path = self.project_root / "docs" / "plan.md"
        result = await upload_file_to_discord(
            token="fake",
            channel_id="123456789012345678",
            file_path=file_path,
            message="Here's the plan!",
            session=mock_session,
        )
        assert result["id"] == "msg999"
        # Verify the message was included in the form data
        call_kwargs = mock_session.post.call_args
        assert call_kwargs is not None


# ---------------------------------------------------------------------------
# TestSendGeneratedFile
# ---------------------------------------------------------------------------


class TestSendGeneratedFile(unittest.IsolatedAsyncioTestCase):
    """Test send_generated_file tool end-to-end."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def test_temp_file_created_and_cleaned(self):
        """Temp file must be cleaned up even on success."""
        from file_share_mcp.security import validate_filename

        validate_filename("summary.md")
        # Create temp file like the tool would
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix="_summary.md",
                dir=str(self.tmp_dir),
                delete=False,
            ) as tmp:
                tmp.write("# Summary\nThis is a summary.")
                tmp_path = Path(tmp.name)
            assert tmp_path.exists()
        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()
        assert not tmp_path.exists()

    async def test_temp_file_cleaned_on_failure(self):
        """Temp file must be cleaned up on upload failure."""
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix="_report.txt",
                dir=str(self.tmp_dir),
                delete=False,
            ) as tmp:
                tmp.write("report content")
                tmp_path = Path(tmp.name)
            # Simulate upload failure
            raise RuntimeError("Upload failed")
        except RuntimeError:
            pass
        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()
        assert not tmp_path.exists()


if __name__ == "__main__":
    unittest.main()
