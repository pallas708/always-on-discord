"""Discord REST API file upload via aiohttp."""

import asyncio
import json
from pathlib import Path

import aiohttp
from mcp.server.fastmcp.exceptions import ToolError

DISCORD_API_BASE = "https://discord.com/api/v10"


async def upload_file_to_discord(
    token: str,
    channel_id: str,
    file_path: Path,
    message: str | None = None,
    max_retries: int = 1,
    session: aiohttp.ClientSession | None = None,
) -> dict:
    """Upload a file to a Discord channel via REST API.

    Args:
        token: Bot token for authorization.
        channel_id: Target channel ID.
        file_path: Path to the file to upload.
        message: Optional text to accompany the attachment.
        max_retries: Number of retries on 429 rate limit.
        session: Optional aiohttp session (for testing). Creates one if None.

    Returns:
        Discord API response dict with message ID.

    Raises:
        ToolError: On permission errors, size limits, or server errors.
    """
    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}"}

    # Read file into memory (safe for files under 10MB cap)
    file_data = file_path.read_bytes()

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))

    try:
        for attempt in range(1 + max_retries):
            # Rebuild FormData each attempt (single-use stream)
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
                    try:
                        data = await resp.json()
                        retry_after = data.get("retry_after", 1.0)
                    except (aiohttp.ContentTypeError, json.JSONDecodeError):
                        retry_after = float(resp.headers.get("Retry-After", "1.0"))
                    await asyncio.sleep(retry_after)
                    continue

                if resp.status == 403:
                    raise ToolError(
                        "I don't have permission to upload files to that channel. "
                        "Check that the bot has the ATTACH_FILES permission."
                    )
                if resp.status == 413:
                    raise ToolError(
                        "The file is too large for Discord. "
                        "Discord's default limit is 10MB for unboosted servers."
                    )
                if resp.status >= 500:
                    raise ToolError(
                        "Discord is temporarily unavailable. Please try again shortly."
                    )

                resp.raise_for_status()
                return await resp.json()
    finally:
        if own_session:
            await session.close()
