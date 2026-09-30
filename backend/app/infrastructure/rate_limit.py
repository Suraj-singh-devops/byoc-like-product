from __future__ import annotations

import contextlib
import threading
import time
from typing import Protocol

import redis

from app.infrastructure.logging import get_logger

log = get_logger(__name__)


class RateLimiter(Protocol):
    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record an attempt; return False when the limit for the window is exceeded."""
        ...

    def reset(self, key: str) -> None: ...


class RedisRateLimiter:
    def __init__(self, client: redis.Redis) -> None:
        self._redis = client

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        full_key = f"byoc:ratelimit:{key}"
        try:
            pipe = self._redis.pipeline()
            pipe.incr(full_key)
            pipe.expire(full_key, window_seconds, nx=True)
            count, _ = pipe.execute()
        except redis.RedisError as exc:
            # Fail open: an unavailable cache must not lock everyone out.
            log.warning("rate_limiter_unavailable", error=str(exc))
            return True
        return int(count) <= limit

    def reset(self, key: str) -> None:
        with contextlib.suppress(redis.RedisError):
            self._redis.delete(f"byoc:ratelimit:{key}")


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            count, started = self._hits.get(key, (0, now))
            if now - started > window_seconds:
                count, started = 0, now
            count += 1
            self._hits[key] = (count, started)
        return count <= limit

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
