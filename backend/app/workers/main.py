"""Backend worker processes (docs/adr/0004):

    python -m app.workers.main cluster-manager     operations (workflows), the reaper; no cloud access
    python -m app.workers.main terraform-runner    terraform tasks: validation, preflight, plan, apply, destroy
    python -m app.workers.main monitoring-worker   read-only tasks and the health monitor
    python -m app.workers.main all                 everything in one process (local tools only)

The role also selects the providers the process builds: only the terraform-runner and the
monitoring-worker get cloud providers, so only they can reach customer accounts.
"""

from __future__ import annotations

import os
import secrets
import signal
import socket
import sys
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

import redis
from prometheus_client import start_http_server

from app.application.platform import Platform
from app.application.provisioning.runner import OperationRunner
from app.application.task_handlers import build_handlers
from app.application.tasks import MONITORING, TERRAFORM, CloudTasks, RedisTaskWaker, TaskRunner
from app.config.settings import get_settings
from app.infrastructure.db import create_db_engine, create_session_factory, wait_for_schema
from app.infrastructure.logging import configure_logging, get_logger
from app.infrastructure.metrics import QUEUE_DEPTH, WORKER_FAILURES
from app.infrastructure.queue import RedisOperationQueue, SafeEnqueuer
from app.infrastructure.rate_limit import RedisRateLimiter
from app.providers.registry import build_registry
from app.workers.monitor import HealthMonitor, RedisLeaderLock
from app.workers.reaper import OperationReaper

log = get_logger("app.workers")
ROLES = ("cluster-manager", "terraform-runner", "monitoring-worker", "all")


def periodic(name: str, fn: Callable[[], object], interval: float, stop: threading.Event) -> threading.Thread:
    def loop() -> None:
        while not stop.is_set():
            started = time.monotonic()
            try:
                fn()
            except Exception:
                log.exception(f"{name}_tick_failed")
                WORKER_FAILURES.labels(name).inc()
            stop.wait(max(0.5, interval - (time.monotonic() - started)))

    thread = threading.Thread(target=loop, name=name, daemon=True)
    thread.start()
    return thread


def serve_operations(platform: Platform, queue: RedisOperationQueue, worker_id: str, stop: threading.Event) -> None:
    runner = OperationRunner(platform, worker_id)
    concurrency = platform.settings.worker_concurrency
    executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="operation")
    slots = threading.BoundedSemaphore(concurrency)

    def done(future: Future[None]) -> None:
        slots.release()
        if future.exception() is not None:
            log.error("operation_thread_failed", error=str(future.exception()))

    while not stop.is_set():
        if not slots.acquire(timeout=1):
            continue
        try:
            operation_id = queue.dequeue(timeout=platform.settings.queue_poll_timeout_seconds)
        except redis.RedisError as exc:
            slots.release()
            log.warning("queue_unavailable", error=str(exc))
            stop.wait(2)
            continue
        if operation_id is None:
            slots.release()
            continue
        executor.submit(runner.run, operation_id).add_done_callback(done)
    executor.shutdown(wait=False, cancel_futures=True)


def serve_tasks(
    platform: Platform, waker: RedisTaskWaker, role: str, worker_id: str, stop: threading.Event
) -> threading.Thread:
    """Claim tasks of one role: on a Redis wake-up, and by scanning PostgreSQL so none is lost."""
    runner = TaskRunner(platform.session_factory, build_handlers(platform), {role}, worker_id)
    concurrency = platform.settings.task_concurrency
    executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix=f"{role}-task")
    slots = threading.BoundedSemaphore(concurrency)

    def execute(task_id: uuid.UUID) -> None:
        try:
            runner.run(task_id)
        finally:
            slots.release()

    def loop() -> None:
        while not stop.is_set():
            if not slots.acquire(timeout=1):
                continue
            try:
                woken = waker.wait(role, timeout=platform.settings.queue_poll_timeout_seconds)
            except redis.RedisError as exc:
                log.warning("task_queue_unavailable", role=role, error=str(exc))
                woken = None
                stop.wait(2)
            task_id = uuid.UUID(woken) if woken else runner.next_pending()
            if task_id is None:
                slots.release()
                continue
            # run() claims with a compare-and-set, so duplicate wake-ups are harmless.
            executor.submit(execute, task_id)

    thread = threading.Thread(target=loop, name=f"{role}-tasks", daemon=True)
    thread.start()
    return thread


def main(argv: list[str]) -> None:
    role = argv[1] if len(argv) > 1 else "all"
    if role not in ROLES:
        raise SystemExit(f"Unknown role {role!r}; choose one of {', '.join(ROLES)}.")
    settings = get_settings().model_copy(update={"service_role": role})
    configure_logging(settings.log_level, settings.log_format, role)
    engine = create_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    client = redis.Redis.from_url(settings.redis_dsn)
    queue = RedisOperationQueue(client)
    waker = RedisTaskWaker(client)
    platform = Platform(
        settings=settings,
        session_factory=session_factory,
        registry=build_registry(settings, session_factory, role),
        queue=SafeEnqueuer(queue),
        rate_limiter=RedisRateLimiter(client),
        tasks=CloudTasks(session_factory, waker),
    )
    wait_for_schema(engine)

    worker_id = f"{role}-{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(3)}"
    start_http_server(settings.worker_metrics_port)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    log.info(
        "worker_started",
        role=role,
        worker_id=worker_id,
        mock_mode=settings.mock_mode,
        cloud_access=platform.registry.has_cloud_access,
    )

    if role in ("terraform-runner", "all"):
        serve_tasks(platform, waker, TERRAFORM, worker_id, stop)
    if role in ("monitoring-worker", "all"):
        serve_tasks(platform, waker, MONITORING, worker_id, stop)
        monitor = HealthMonitor(
            platform,
            RedisLeaderLock(client, "byoc:leader:monitor", worker_id, int(settings.monitor_interval_seconds * 3)),
        )
        periodic("monitor", monitor.tick, settings.monitor_interval_seconds, stop)
    if role in ("cluster-manager", "all"):
        reaper = OperationReaper(platform)

        def reap() -> None:
            reaper.tick()
            QUEUE_DEPTH.set(queue.depth())

        periodic("reaper", reap, settings.reaper_interval_seconds, stop)
        serve_operations(platform, queue, worker_id, stop)
    else:
        stop.wait()
    log.info("worker_stopping", role=role, note="unfinished work resumes elsewhere after its lease expires")


if __name__ == "__main__":
    main(sys.argv)
