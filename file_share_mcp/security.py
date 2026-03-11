"""Security layer for file-share MCP server.

Path validation, deny list, channel validation, size checks, filename sanitization,
and file listing with filtering.
"""

import fnmatch
import os
import re
from pathlib import Path

from mcp.server.fastmcp.exceptions import ToolError

from .config import FileSharingConfig

# Strict regex for generated filenames: alphanumeric/dash/underscore, 1-100 chars,
# dot, lowercase extension 1-5 chars.
_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,100}\.[a-z]{1,5}$")

# Only text-based extensions are allowed for generated files.
_ALLOWED_EXTENSIONS = {"md", "txt", "py", "json", "yaml", "csv"}


def validate_path(
    requested: str,
    allowed_paths: list[Path],
    project_root: Path,
) -> Path:
    """Validate a file path against allowed paths.

    Uses os.sep boundary check to prevent prefix attacks (CVE-2025-53109).
    Rejects symlinks outright. Rejects null bytes.

    Args:
        requested: File path (absolute or relative to project_root).
        allowed_paths: List of allowed directory/file Paths (already absolute).
        project_root: Project root for resolving relative paths.

    Returns:
        Resolved Path if valid.

    Raises:
        ToolError: If path is rejected.
    """
    if "\x00" in requested:
        raise ToolError("Access denied: path contains null bytes.")

    # Resolve relative paths against project root
    req_path = Path(requested)
    if not req_path.is_absolute():
        req_path = project_root / req_path

    resolved = Path(os.path.realpath(str(req_path)))

    # Reject symlinks
    if req_path.is_symlink():
        raise ToolError("Access denied: symlinks are not allowed.")

    # Check file exists
    if not resolved.exists():
        raise ToolError(
            f"File not found: {requested}. "
            f"Use list_shareable_files to browse available files."
        )

    # Check against each allowed path
    for allowed in allowed_paths:
        allowed_resolved = Path(os.path.realpath(str(allowed)))

        # Exact match (works for both files and directories)
        if resolved == allowed_resolved:
            return resolved

        # Directory containment check with os.sep boundary
        if resolved.as_posix().startswith(allowed_resolved.as_posix() + "/"):
            return resolved

    allowed_strs = [
        str(p.relative_to(project_root)) if p.is_relative_to(project_root) else str(p)
        for p in allowed_paths
    ]
    raise ToolError(
        f"Access denied: file is not in an allowed path. "
        f"Allowed paths: {', '.join(allowed_strs)}"
    )


def is_denied(filename: str, denied_files: list[str]) -> bool:
    """Check if a filename matches any deny list pattern.

    Uses fnmatch on the basename only.
    """
    for pattern in denied_files:
        if fnmatch.fnmatch(filename, pattern):
            return True
    return False


def validate_channel(channel_id: str, allowed_channels: list[str]) -> None:
    """Validate channel_id against the configured channels list.

    Raises ToolError if the channel is not monitored.
    """
    if channel_id not in allowed_channels:
        raise ToolError(
            f"Access denied: '{channel_id}' is not a monitored channel. "
            f"You can only send files to channels the bot monitors."
        )


def check_file_size(file_path: Path, max_bytes: int) -> None:
    """Check that a file does not exceed the size limit.

    Raises ToolError if file size >= max_bytes.
    """
    size = file_path.stat().st_size
    if size >= max_bytes:
        max_mb = max_bytes / (1024 * 1024)
        file_mb = size / (1024 * 1024)
        raise ToolError(
            f"File too large: {file_mb:.1f}MB (limit: {max_mb:.0f}MB). "
            f"Consider splitting or compressing the file."
        )


def validate_filename(filename: str) -> None:
    """Validate a generated filename against strict rules.

    Only allows: alphanumeric, dash, underscore (1-100 chars), dot,
    lowercase extension (1-5 chars) from the allowed set.

    Raises ToolError if invalid.
    """
    if not _FILENAME_RE.match(filename):
        raise ToolError(
            f"Invalid filename: '{filename}'. "
            f"Use only letters, numbers, dashes, underscores, "
            f"with a lowercase extension (e.g., summary.md)."
        )

    ext = filename.rsplit(".", 1)[1]
    if ext not in _ALLOWED_EXTENSIONS:
        raise ToolError(
            f"Extension '.{ext}' is not allowed. "
            f"Allowed extensions: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
        )


def list_files(
    config: FileSharingConfig,
    directory: str | None = None,
    pattern: str | None = None,
    max_results: int = 50,
    include_metadata: bool = False,
) -> dict:
    """List shareable files from allowed directories.

    Args:
        config: FileSharingConfig instance.
        directory: Optional subdirectory to filter (relative to project root).
        pattern: Optional glob pattern (e.g., "*.md").
        max_results: Max files to return (hard cap: 200).
        include_metadata: If True, include size_bytes and modified per file.

    Returns:
        Dict with files list, truncated flag, and total_available count.
    """
    max_results = min(max_results, 200)

    # Determine which paths to scan
    if directory:
        # Validate the directory against allowed paths
        dir_path = config.project_root / directory
        dir_resolved = Path(os.path.realpath(str(dir_path)))
        allowed = False
        for ap in config.allowed_paths:
            ap_resolved = Path(os.path.realpath(str(ap)))
            if dir_resolved == ap_resolved or dir_resolved.as_posix().startswith(ap_resolved.as_posix() + "/"):
                allowed = True
                break
        if not allowed:
            raise ToolError(
                f"Access denied: '{directory}' is not in an allowed path."
            )
        scan_paths = [dir_path]
    else:
        scan_paths = config.allowed_paths

    # Collect files
    all_files = []
    for path in scan_paths:
        resolved = Path(os.path.realpath(str(path)))
        if resolved.is_dir():
            for f in resolved.rglob("*"):
                if f.is_file() and not f.is_symlink():
                    all_files.append(f)
        elif resolved.is_file() and not resolved.is_symlink():
            all_files.append(resolved)

    # Filter deny list
    all_files = [f for f in all_files if not is_denied(f.name, config.denied_files)]

    # Filter by glob pattern
    if pattern:
        all_files = [f for f in all_files if fnmatch.fnmatch(f.name, pattern)]

    total_available = len(all_files)
    truncated = total_available > max_results
    result_files = all_files[:max_results]

    # Build response
    files_out = []
    for f in result_files:
        try:
            rel = f.relative_to(config.project_root)
        except ValueError:
            rel = f
        entry = {"path": str(rel), "name": f.name}
        if include_metadata:
            stat = f.stat()
            entry["size_bytes"] = stat.st_size
            entry["modified"] = stat.st_mtime
        files_out.append(entry)

    return {
        "files": files_out,
        "truncated": truncated,
        "total_available": total_available,
    }
