"""Sliding-window rate limiter for file sharing operations."""

import time

from mcp.server.fastmcp.exceptions import ToolError


class RateLimiter:
    """Sliding-window rate limiter.

    Enforces two limits:
    - Global: max_global ops per window across all channels
    - Per-channel: max_per_channel ops per window per channel_id
    """

    def __init__(
        self,
        max_global: int = 10,
        max_per_channel: int = 5,
        window_seconds: float = 60.0,
    ):
        self.max_global = max_global
        self.max_per_channel = max_per_channel
        self.window_seconds = window_seconds
        self._global_timestamps: list[float] = []
        self._channel_timestamps: dict[str, list[float]] = {}

    def _prune(self, timestamps: list[float], now: float) -> list[float]:
        cutoff = now - self.window_seconds
        return [t for t in timestamps if t > cutoff]

    def check(self, channel_id: str) -> None:
        """Check rate limits and record the operation.

        Raises ToolError if either limit is exceeded.
        """
        now = time.monotonic()

        # Prune and check global limit
        self._global_timestamps = self._prune(self._global_timestamps, now)
        if len(self._global_timestamps) >= self.max_global:
            raise ToolError(
                f"Rate limited: max {self.max_global} file operations "
                f"per minute globally. Please wait before trying again."
            )

        # Prune and check per-channel limit
        ch_ts = self._channel_timestamps.get(channel_id, [])
        ch_ts = self._prune(ch_ts, now)
        if len(ch_ts) >= self.max_per_channel:
            raise ToolError(
                f"Rate limited: max {self.max_per_channel} file operations "
                f"per minute per channel. Please wait before trying again."
            )

        # Record this operation
        self._global_timestamps.append(now)
        ch_ts.append(now)
        self._channel_timestamps[channel_id] = ch_ts
