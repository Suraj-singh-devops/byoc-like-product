"""Cloud tasks: how processes without cloud access hand work to the ones that have it.

    API / cluster-manager                     terraform-runner / monitoring-worker
    ---------------------                     ------------------------------------
    submit(kind, payload) -> row PENDING  ->  wake (Redis list per role)
                                              claim: compare-and-set PENDING -> RUNNING
    wait(): poll the row, copy progress   <-  progress, heartbeat, result or error
            into the operation log

PostgreSQL is the record; Redis only wakes a worker, which also scans for pending tasks, so a lost
wake-up only delays a task. A task whose worker stops heartbeating is failed by the waiter, and
the operation can be retried (every step is idempotent). A task belonging to an operation checks
the operation's cancellation at safe points and ends with OPERATION_CANCELLED.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import redis
from sqlalchemy import select, update

from app.domain import errors as domain_errors
from app.domain.errors import OperationCancelled, PlatformError, ProvisioningError
from app.infrastructure.db import SessionFactory, session_scope
from app.infrastructure.logging import get_logger
from app.models import CloudTask, Operation

log = get_logger(__name__)

TERRAFORM = "terraform"
MONITORING = "monitoring"
# Which worker runs each kind of task: writes go to the terraform-runner, reads to the monitoring-worker.
KIND_ROLES: dict[str, str] = {
    "validate_account": TERRAFORM,
    "preflight": TERRAFORM,
    "plan": TERRAFORM,
    "apply": TERRAFORM,
    "destroy": TERRAFORM,
    "describe_network": MONITORING,
    "validate_network": MONITORING,
    "refresh_cluster": MONITORING,
}
PENDING, RUNNING, SUCCEEDED, FAILED = "PENDING", "RUNNING", "SUCCEEDED", "FAILED"
MAX_PROGRESS = 200


def utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------- errors


def error_to_dict(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, OperationCancelled):
        return {"type": "OperationCancelled", "code": "OPERATION_CANCELLED", "message": "Cancelled"}
    if isinstance(exc, PlatformError):
        return {"type": type(exc).__name__, "status_code": exc.status_code, **exc.to_dict()}
    return {"type": "PlatformError", "code": "INTERNAL_ERROR", "message": "The worker failed unexpectedly."}


class TaskCrashed(RuntimeError):
    """The worker hit an unexpected error; reported like any internal error, without details."""


def error_from_dict(data: dict[str, Any]) -> BaseException:
    if data.get("type") == "OperationCancelled":
        return OperationCancelled()
    if data.get("code") == "INTERNAL_ERROR":
        return TaskCrashed("A cloud task failed unexpectedly.")
    cls = getattr(domain_errors, str(data.get("type")), PlatformError)
    if not (isinstance(cls, type) and issubclass(cls, PlatformError)):
        cls = PlatformError
    return cls(
        str(data.get("message") or "The task failed."),
        code=data.get("code"),
        reason=data.get("reason"),
        suggested_action=data.get("suggested_action"),
        details=data.get("details"),
        status_code=data.get("status_code"),
    )


# ------------------------------------------------------------------ wake-ups


class TaskWaker(Protocol):
    def wake(self, role: str, task_id: str) -> None: ...

    def wait(self, role: str, timeout: float) -> str | None: ...


class RedisTaskWaker:
    def __init__(self, client: redis.Redis) -> None:
        self._redis = client

    @staticmethod
    def key(role: str) -> str:
        return f"byoc:queue:tasks:{role}"

    def wake(self, role: str, task_id: str) -> None:
        try:
            self._redis.lpush(self.key(role), task_id)
        except redis.RedisError as exc:
            # The worker's periodic scan picks the task up anyway.
            log.warning("task_wake_failed", role=role, error=str(exc))

    def wait(self, role: str, timeout: float) -> str | None:
        item = self._redis.brpop([self.key(role)], timeout=max(1, int(timeout)))
        if item is None:
            return None
        value = item[1]
        return value.decode() if isinstance(value, bytes) else str(value)


# --------------------------------------------------------------------- client


class CloudTasks:
    """Submit tasks and wait for them. ``inline`` runs each task immediately in this process
    (tests and single-process tools); the protocol and the stored rows are the same."""

    def __init__(
        self,
        session_factory: SessionFactory,
        waker: TaskWaker | None = None,
        *,
        inline: TaskRunner | None = None,
        poll_interval: float = 0.5,
        stale_after_seconds: float = 120.0,
    ) -> None:
        self.session_factory = session_factory
        self.waker = waker
        self.inline = inline
        self.poll_interval = poll_interval
        self.stale_after = timedelta(seconds=stale_after_seconds)

    def submit(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        organization_id: uuid.UUID,
        operation_id: uuid.UUID | None = None,
        requested_by_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        role = KIND_ROLES[kind]
        with session_scope(self.session_factory) as s:
            task = CloudTask(
                organization_id=organization_id,
                operation_id=operation_id,
                role=role,
                kind=kind,
                status=PENDING,
                payload=payload,
                progress=[],
                requested_by_id=requested_by_id,
            )
            s.add(task)
            s.flush()
            task_id = task.id
        if self.inline is not None:
            self.inline.run(task_id)
        elif self.waker is not None:
            self.waker.wake(role, str(task_id))
        return task_id

    def wait(
        self,
        task_id: uuid.UUID,
        *,
        timeout: float,
        on_message: Callable[[str], None] | None = None,
        on_poll: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        seen = 0
        while True:
            with session_scope(self.session_factory) as s:
                task = s.get(CloudTask, task_id)
                assert task is not None
                progress = list(task.progress or [])
                status, result, error, kind = task.status, task.result, task.error, task.kind
                stale = (
                    status == RUNNING
                    and task.heartbeat_at is not None
                    and utcnow() - task.heartbeat_at > self.stale_after
                )
            for entry in progress[seen:]:
                if on_message is not None:
                    on_message(str(entry.get("message", "")))
            seen = len(progress)
            if status == SUCCEEDED:
                return dict(result or {})
            if status == FAILED:
                raise error_from_dict(error or {})
            if stale or time.monotonic() > deadline:
                self._abandon(task_id, "the worker stopped responding" if stale else "it did not finish in time")
                raise ProvisioningError(
                    f"The {kind} task did not complete: "
                    + ("the worker stopped responding." if stale else "it timed out."),
                    code="TASK_ABANDONED" if stale else "TASK_TIMEOUT",
                    suggested_action="Retry the operation; every step resumes safely.",
                    details={"task_id": str(task_id)},
                )
            if on_poll is not None:
                on_poll()
            time.sleep(self.poll_interval)

    def run(self, kind: str, payload: dict[str, Any], *, timeout: float, **kwargs: Any) -> dict[str, Any]:
        on_message = kwargs.pop("on_message", None)
        on_poll = kwargs.pop("on_poll", None)
        task_id = self.submit(kind, payload, **kwargs)
        return self.wait(task_id, timeout=timeout, on_message=on_message, on_poll=on_poll)

    def _abandon(self, task_id: uuid.UUID, why: str) -> None:
        with session_scope(self.session_factory) as s:
            s.execute(
                update(CloudTask)
                .where(CloudTask.id == task_id, CloudTask.status.in_([PENDING, RUNNING]))
                .values(
                    status=FAILED,
                    completed_at=utcnow(),
                    error={"type": "ProvisioningError", "code": "TASK_ABANDONED", "message": f"Abandoned: {why}."},
                )
            )


# ------------------------------------------------------------------- progress


class TaskProgress:
    """ProgressReporter for providers running inside a task: messages and heartbeats go to the task
    row; cancellation is read from the task's operation."""

    FLUSH_SECONDS = 1.0
    CANCEL_CHECK_SECONDS = 2.0

    def __init__(self, session_factory: SessionFactory, task_id: uuid.UUID, operation_id: uuid.UUID | None) -> None:
        self.session_factory = session_factory
        self.task_id = task_id
        self.operation_id = operation_id
        self._pending: list[dict[str, Any]] = []
        self._last_flush = 0.0
        self._last_cancel_check = 0.0

    def message(self, text: str) -> None:
        self._pending.append({"at": utcnow().isoformat(), "message": text})
        self._flush()

    def heartbeat(self) -> None:
        self._flush()

    def flush(self) -> None:
        self._flush(force=True)

    def _flush(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_flush < self.FLUSH_SECONDS:
            return
        self._last_flush = now
        with session_scope(self.session_factory) as s:
            task = s.get(CloudTask, self.task_id)
            if task is None:
                return
            if self._pending:
                task.progress = (list(task.progress or []) + self._pending)[-MAX_PROGRESS:]
            task.heartbeat_at = utcnow()
        self._pending = []

    def check_cancelled(self) -> None:
        if self.operation_id is None:
            return
        now = time.monotonic()
        if now - self._last_cancel_check < self.CANCEL_CHECK_SECONDS:
            return
        self._last_cancel_check = now
        with session_scope(self.session_factory) as s:
            requested = s.scalar(select(Operation.cancel_requested).where(Operation.id == self.operation_id))
        if requested:
            raise OperationCancelled()

    def sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while True:
            self.check_cancelled()
            self.heartbeat()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.5))


# ---------------------------------------------------------------------- runner

Handler = Callable[["TaskContext"], dict[str, Any]]


class TaskContext:
    def __init__(self, task: CloudTask, progress: TaskProgress) -> None:
        self.task_id = task.id
        self.organization_id = task.organization_id
        self.operation_id = task.operation_id
        self.requested_by_id = task.requested_by_id
        self.kind = task.kind
        self.payload = dict(task.payload or {})
        self.progress = progress


class TaskRunner:
    """Claims and runs tasks of the given roles in a process that has cloud access."""

    def __init__(
        self, session_factory: SessionFactory, handlers: dict[str, Handler], roles: set[str], worker_id: str
    ) -> None:
        self.session_factory = session_factory
        self.handlers = handlers
        self.roles = roles
        self.worker_id = worker_id

    def claim(self, task_id: uuid.UUID) -> bool:
        now = utcnow()
        with session_scope(self.session_factory) as s:
            result = s.execute(
                update(CloudTask)
                .where(CloudTask.id == task_id, CloudTask.status == PENDING, CloudTask.role.in_(self.roles))
                .values(status=RUNNING, worker_id=self.worker_id, started_at=now, heartbeat_at=now, updated_at=now)
                .execution_options(synchronize_session=False)
            )
            return result.rowcount == 1

    def next_pending(self) -> uuid.UUID | None:
        with session_scope(self.session_factory) as s:
            return s.scalar(
                select(CloudTask.id)
                .where(CloudTask.status == PENDING, CloudTask.role.in_(self.roles))
                .order_by(CloudTask.created_at)
                .limit(1)
            )

    def run(self, task_id: uuid.UUID) -> bool:
        if not self.claim(task_id):
            return False
        with session_scope(self.session_factory) as s:
            task = s.get(CloudTask, task_id)
            assert task is not None
            progress = TaskProgress(self.session_factory, task.id, task.operation_id)
            context = TaskContext(task, progress)
        handler = self.handlers.get(context.kind)
        status, result, error = SUCCEEDED, None, None
        try:
            if handler is None:
                raise PlatformError(f"No handler for task {context.kind}.", code="UNKNOWN_TASK")
            result = handler(context)
        except (PlatformError, OperationCancelled) as exc:
            status, error = FAILED, error_to_dict(exc)
        except Exception as exc:
            log.exception("task_crashed", task_id=str(task_id), kind=context.kind)
            status, error = FAILED, error_to_dict(exc)
        progress.flush()
        with session_scope(self.session_factory) as s:
            s.execute(
                update(CloudTask)
                .where(CloudTask.id == task_id, CloudTask.status == RUNNING)
                .values(status=status, result=result, error=error, completed_at=utcnow(), heartbeat_at=utcnow())
            )
        return True
