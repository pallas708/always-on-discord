"""Configuration for the file-share MCP server."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileSharingConfig:
    """Typed configuration for file sharing operations."""

    allowed_paths: list[Path]
    allowed_channels: list[str]
    denied_files: list[str]
    max_file_size_bytes: int
    project_root: Path
    temp_dir: Path

    def __post_init__(self):
        if self.max_file_size_bytes <= 0:
            raise ValueError("max_file_size_bytes must be positive")
        if not self.project_root.exists():
            raise ValueError(f"project_root does not exist: {self.project_root}")
        if not self.allowed_channels:
            raise ValueError("allowed_channels must not be empty")

    @classmethod
    def from_config(cls, config: dict, project_root: Path) -> "FileSharingConfig":
        """Build from parsed config.yaml dict.

        Handles int-to-str channel ID conversion (YAML stores ints,
        MCP tool parameters use strings).
        """
        fs = config.get("file_sharing", {})
        return cls(
            allowed_paths=[
                project_root / p for p in fs.get("allowed_paths", [])
            ],
            allowed_channels=[str(ch) for ch in config["channels"]],
            denied_files=fs.get("denied_files", []),
            max_file_size_bytes=fs.get("max_file_size_mb", 10) * 1024 * 1024,
            project_root=project_root,
            temp_dir=project_root / "tmp",
        )
