"""File-share MCP server entry point.

Exposes three tools: list_shareable_files, send_file, send_generated_file.
Communicates over stdio JSON-RPC, managed by Claude CLI.
"""

import os
import sys
import tempfile
from pathlib import Path

import yaml
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.fastmcp.exceptions import ToolError

from .cleanup import cleanup_stale_temps
from .config import FileSharingConfig
from .discord_upload import upload_file_to_discord
from .rate_limiter import RateLimiter
from .security import (
    check_file_size,
    is_denied,
    list_files,
    validate_channel,
    validate_filename,
    validate_path,
)

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP("FileSharing")

# Config loaded from environment variables set in discord-mcp.json
_config_path = os.environ.get("FILE_SHARE_CONFIG", "config.yaml")
_project_root = Path(os.environ.get("FILE_SHARE_PROJECT_ROOT", Path(__file__).resolve().parent.parent))

# Load .env from project root for DISCORD_BOT_TOKEN
# (MCP config env sets DISCORD_TOKEN="" which overwrites the real token)
load_dotenv(Path(_project_root) / ".env")

_raw_config = yaml.safe_load(Path(_config_path).read_text())
CONFIG = FileSharingConfig.from_config(_raw_config, Path(_project_root))

# Ensure temp directory exists
CONFIG.temp_dir.mkdir(mode=0o700, exist_ok=True)

# Rate limiter instance
_rate_limiter = RateLimiter(max_global=10, max_per_channel=5, window_seconds=60.0)

# Discord bot token — loaded from .env (DISCORD_BOT_TOKEN)
_discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")

# Module-level aiohttp session (lazy init in upload function)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_shareable_files(
    directory: str | None = None,
    pattern: str | None = None,
    max_results: int = 50,
    include_metadata: bool = False,
    ctx: Context = None,
) -> dict:
    """Browse available files in allowed directories.

    Args:
        directory: Optional subdirectory path relative to project root.
        pattern: Optional glob pattern to filter (e.g., "*.md", "*.pdf").
        max_results: Maximum files to return (default 50, hard cap 200).
        include_metadata: If true, include size_bytes and modified timestamp.

    Returns:
        Dict with files list, truncated flag, and total_available count.
    """
    if ctx:
        await ctx.info(f"Listing files: directory={directory}, pattern={pattern}")

    return list_files(
        CONFIG,
        directory=directory,
        pattern=pattern,
        max_results=max_results,
        include_metadata=include_metadata,
    )


@mcp.tool()
async def send_file(
    file_path: str,
    channel_id: str,
    message: str | None = None,
    ctx: Context = None,
) -> dict:
    """Upload an existing file to a Discord channel as an attachment.

    Args:
        file_path: Path to the file (absolute or relative to project root).
        channel_id: Discord channel ID to send to.
        message: Optional text to accompany the file attachment.

    Returns:
        Dict with success status, filename, size_bytes, message_id, channel_id.
    """
    # Security checks
    validate_channel(channel_id, CONFIG.allowed_channels)
    resolved = validate_path(file_path, CONFIG.allowed_paths, CONFIG.project_root)

    if is_denied(resolved.name, CONFIG.denied_files):
        raise ToolError(
            f"Access denied: '{resolved.name}' is on the deny list and cannot be shared."
        )

    check_file_size(resolved, CONFIG.max_file_size_bytes)
    _rate_limiter.check(channel_id)

    if ctx:
        await ctx.info(f"Uploading {resolved.name} to channel {channel_id}")

    result = await upload_file_to_discord(
        token=_discord_token,
        channel_id=channel_id,
        file_path=resolved,
        message=message,
    )

    size_bytes = resolved.stat().st_size
    if ctx:
        await ctx.info(f"Uploaded {resolved.name} ({size_bytes} bytes)")

    return {
        "success": True,
        "filename": resolved.name,
        "size_bytes": size_bytes,
        "message_id": result.get("id"),
        "channel_id": channel_id,
    }


@mcp.tool()
async def send_generated_file(
    content: str,
    filename: str,
    channel_id: str,
    message: str | None = None,
    ctx: Context = None,
) -> dict:
    """Create and upload a new text file to a Discord channel.

    Use for summaries, reports, code snippets, or other generated content.

    Args:
        content: The text content to share as a file.
        filename: Desired filename (e.g., "summary.md", "report.txt").
        channel_id: Discord channel ID to send to.
        message: Optional text to accompany the file attachment.

    Returns:
        Dict with success status, filename, size_bytes, message_id, channel_id.
    """
    # Security checks
    validate_channel(channel_id, CONFIG.allowed_channels)
    validate_filename(filename)
    _rate_limiter.check(channel_id)

    # Check content size
    content_bytes = content.encode("utf-8")
    if len(content_bytes) >= CONFIG.max_file_size_bytes:
        max_mb = CONFIG.max_file_size_bytes / (1024 * 1024)
        raise ToolError(
            f"Generated content too large: {len(content_bytes)} bytes "
            f"(limit: {max_mb:.0f}MB)."
        )

    if ctx:
        await ctx.info(f"Creating temp file: {filename}")

    # Write to temp file and upload
    tmp_path: Path | None = None
    try:
        CONFIG.temp_dir.mkdir(mode=0o700, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=f"_{filename}",
            dir=str(CONFIG.temp_dir),
            delete=False,
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        result = await upload_file_to_discord(
            token=_discord_token,
            channel_id=channel_id,
            file_path=tmp_path,
            message=message,
        )

        if ctx:
            await ctx.info(f"Uploaded generated file: {filename} ({len(content_bytes)} bytes)")

        return {
            "success": True,
            "filename": filename,
            "size_bytes": len(content_bytes),
            "message_id": result.get("id"),
            "channel_id": channel_id,
        }
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cleanup_stale_temps(CONFIG.temp_dir)
    mcp.run(transport="stdio")
