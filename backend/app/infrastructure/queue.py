"""Work queue between the API and the workers.

The queue only carries operation IDs. PostgreSQL is the source of truth: a worker must
claim an operation with a compare-and-set on its status, so duplicate or replayed queue
messages are harmless, and the reaper re-enqueues anything that was lost.
"""

from __future__ import annotations

import queue as stdlib_queue
from typing import Protocol

import redis

from app.infrastructure.logging import get_logger

log = get_logger(__name__)

QUEUE_KEY = "byoc:queue:operations"


class OperationQueue(Protocol):
    def enqueue(self, operation_id: str) -> None: ...

    def dequeue(self, timeout: float) -> str | None: ...

    def contains(self, operation_id: str) -> bool: ...


class RedisOperationQueue:
    def __init__(self, client: redis.Redis) -> None:
        self._redis = client

    def enqueue(self, operation_id: str) -> None:
        self._redis.lpush(QUEUE_KEY, operation_id)

    def dequeue(self, timeout: float) -> str | None:
        item = self._redis.brpop([QUEUE_KEY], timeout=max(1, int(timeout)))
        if item is None:
            return None
        value = item[1]
        return value.decode() if isinstance(value, bytes) else str(value)

    def depth(self) -> int:
        return int(self._redis.llen(QUEUE_KEY))

    def contains(self, operation_id: str) -> bool:
        return self._redis.lpos(QUEUE_KEY, operation_id) is not None


class InMemoryOperationQueue:
    """Used by tests and by single-process setups."""

    def __init__(self) -> None:
        self._q: stdlib_queue.Queue[str] = stdlib_queue.Queue()

    def enqueue(self, operation_id: str) -> None:
        self._q.put(operation_id)

    def dequeue(self, timeout: float) -> str | None:
        try:
            return self._q.get(timeout=timeout) if timeout > 0 else self._q.get_nowait()
        except stdlib_queue.Empty:
            return None

    def contains(self, operation_id: str) -> bool:
        with self._q.mutex:
            return operation_id in self._q.queue

    def drain(self) -> list[str]:
        items: list[str] = []
        while True:
            try:
                items.append(self._q.get_nowait())
            except stdlib_queue.Empty:
                return items


class SafeEnqueuer:
    """Enqueue without failing the request if Redis is briefly unavailable.

    The operation row is already committed; the reaper will pick it up later.
    """

    def __init__(self, inner: OperationQueue) -> None:
        self._inner = inner

    def enqueue(self, operation_id: str) -> None:
        try:
            self._inner.enqueue(operation_id)
        except Exception as exc:
            log.warning("enqueue_failed_will_retry", operation_id=operation_id, error=str(exc))

    def dequeue(self, timeout: float) -> str | None:
        return self._inner.dequeue(timeout)

    def contains(self, operation_id: str) -> bool:
        try:
            return self._inner.contains(operation_id)
        except Exception:
            return False
