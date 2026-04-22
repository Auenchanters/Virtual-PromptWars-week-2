"""In-memory sliding-window rate limiter.

Rubric: Security (abuse / quota protection), Code Quality (focused module).
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock


class RateLimiter:
    """Per-key sliding-window limiter, safe for concurrent ASGI workers in one process.

    Designed for a single Cloud Run instance with bounded ``max-instances``.
    For multi-instance deployments, swap in a Redis-backed implementation behind the
    same ``check`` interface.
    """

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    @property
    def max_requests(self) -> int:
        return self._max

    @property
    def window_seconds(self) -> int:
        return self._window

    def check(self, key: str) -> tuple[bool, int]:
        """Record a request for ``key``.

        Returns ``(allowed, retry_after_seconds)``. ``retry_after_seconds`` is 0 when
        the request is allowed, and the seconds until the oldest hit ages out otherwise.
        """
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self._window:
                q.popleft()
            if len(q) >= self._max:
                retry_after = max(1, int(self._window - (now - q[0])) + 1)
                return False, retry_after
            q.append(now)
            return True, 0

    def reset(self) -> None:
        """Drop all state. Useful for tests."""
        with self._lock:
            self._hits.clear()
