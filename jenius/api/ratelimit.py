"""A small sliding-window rate limiter kept in memory.

It is per process: with several workers or machines each keeps its own count, so the effective
limit is a multiple of the one set here. That's fine for brute-force protection; the Claude
message quota that protects the bill is counted in the database instead.
"""
import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._clock = clock

    def hit(self, key: str, limit: int, window_seconds: float) -> float | None:
        """Record one request. Returns None if allowed, or how many seconds to wait."""
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return max(1.0, hits[0] + window_seconds - now)
            hits.append(now)
            if len(self._hits) > 50_000:  # forget idle keys so memory stays bounded
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - 3600]:
                    del self._hits[k]
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
