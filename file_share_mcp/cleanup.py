"""Temp file cleanup for the file-share MCP server."""

import sys
from pathlib import Path


def cleanup_stale_temps(tmp_dir: Path) -> None:
    """Delete ALL files in tmp/ on startup.

    The tmp/ directory is exclusively owned by this MCP server. Any files
    present at startup are orphans from a previous crash.
    """
    if not tmp_dir.exists():
        return
    for f in tmp_dir.iterdir():
        if f.is_file():
            f.unlink()
            print(f"Cleaned up orphaned temp file: {f.name}", file=sys.stderr)
