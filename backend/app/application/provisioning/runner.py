from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import aliased

from app.application.health_service import HealthService
from app.application.platform import Platform
from app.application.provisioning.context import OperationContext
from app.application.provisioning.outcome import finalize_operation, waiting_operations
from app.application.provisioning.workflows import WORKFLOWS
from app.domain.enums import MUTATING_OPERATION_TYPES
from app.domain.errors import NotSupported, OperationCancelled, PlatformError
from app.domain.states import RUNNING_OPERATION_STATUSES, OperationStatus
from app.infrastructure.db import session_scope
from app.infrastructure.logging import bind_context, get_logger
from app.infrastructure.metrics import WORKER_FAILURES
from app.models import Operation

log = get_logger(__name__)


class LeaseKeeper:
    """Refresh ``heartbeat_at`` while an operation runs, so the reaper can tell a slow
    operation (e.g. a long Terraform apply) from one whose worker died."""

    def __init__(self, platform: Platform, operation_id: uuid.UUID, worker_id: str) -> None:
        self.platform = platform
        self.operation_id = operation_id
        self.worker_id = worker_id
        self.interval = max(1.0, platform.settings.operation_lease_seconds / 4)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"lease-{operation_id}", daemon=True)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                with session_scope(self.platform.session_factory) as s:
                    s.execute(
                        update(Operation)
                        .where(Operation.id == self.operation_id, Operation.worker_id == self.worker_id)
                        .values(heartbeat_at=datetime.now(UTC))
                    )
            except Exception:
                log.exception("lease_refresh_failed", operation_id=str(self.operation_id))

    def __enter__(self) -> LeaseKeeper:
        self._thread.start()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


class OperationRunner:
    def __init__(self, platform: Platform, worker_id: str) -> None:
        self.platform = platform
        self.worker_id = worker_id
        self.health = HealthService(platform)

    def claim(self, operation_id: uuid.UUID) -> bool:
        """Compare-and-set PENDING -> VALIDATING.

        A mutating operation is not claimed while another mutation of the same cluster is still
        running: a delete waits for the operation it pre-empted (docs/adr/0010). It is claimed
        once that operation finishes and wakes it.
        """
        now = datetime.now(UTC)
        other = aliased(Operation)
        busy = (
            select(other.id)
            .where(
                other.cluster_id == Operation.cluster_id,
                other.id != Operation.id,
                other.status.in_([st.value for st in RUNNING_OPERATION_STATUSES]),
                other.operation_type.in_([t.value for t in MUTATING_OPERATION_TYPES]),
            )
            .exists()
        )
        with session_scope(self.platform.session_factory) as s:
            result = s.execute(
                update(Operation)
                .where(
                    Operation.id == operation_id,
                    Operation.status == OperationStatus.PENDING.value,
                    Operation.cancel_requested.is_(False),
                    or_(Operation.operation_type.not_in([t.value for t in MUTATING_OPERATION_TYPES]), ~busy),
                )
                .values(
                    status=OperationStatus.VALIDATING.value,
                    worker_id=self.worker_id,
                    heartbeat_at=now,
                    started_at=func.coalesce(Operation.started_at, now),
                    attempt=Operation.attempt + 1,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            return result.rowcount == 1

    def run(self, operation_id: str) -> None:
        try:
            op_id = uuid.UUID(operation_id)
        except ValueError:
            log.warning("invalid_operation_id_in_queue", value=operation_id)
            return
        if not self.claim(op_id):
            log.info("operation_not_claimable", operation_id=operation_id)
            return
        with bind_context(operation_id=operation_id), LeaseKeeper(self.platform, op_id, self.worker_id):
            ctx = OperationContext(self.platform, op_id)
            with bind_context(cluster_id=ctx.cluster_id, operation_type=ctx.operation_type):
                self._execute(ctx)

    def _execute(self, ctx: OperationContext) -> None:
        log.info("operation_started", attempt=ctx.attempt)
        workflow_cls = WORKFLOWS.get(ctx.operation_type)
        try:
            if workflow_cls is None:
                raise NotSupported(f"{ctx.operation_type} operations are not available in this release.")
            result = workflow_cls(ctx, self.platform, self.health).run()
            self._finish(ctx, OperationStatus.COMPLETED, result=result)
        except OperationCancelled:
            self._finish(ctx, OperationStatus.CANCELLED)
        except PlatformError as exc:
            log.warning("operation_failed", error_code=exc.code, error=str(exc))
            self._finish(ctx, OperationStatus.FAILED, error=exc)
        except Exception:
            log.exception("operation_crashed")
            WORKER_FAILURES.labels("operation").inc()
            error = PlatformError(
                "An internal error occurred while running the operation.",
                code="INTERNAL_ERROR",
                reason=f"Reference: operation {ctx.operation_id}",
                suggested_action="Retry the operation. If it fails again, contact support with the operation ID.",
                status_code=500,
            )
            self._finish(ctx, OperationStatus.FAILED, error=error)

    def _finish(
        self,
        ctx: OperationContext,
        status: OperationStatus,
        *,
        error: PlatformError | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        ctx.flush()
        with session_scope(self.platform.session_factory) as s:
            op = s.get(Operation, ctx.operation_id)
            if op is None:
                return
            finalize_operation(s, op, status, error=error, result=result if result is not None else ctx.result)
        wake_waiting(self.platform, ctx.cluster_id)


def wake_waiting(platform: Platform, cluster_id: uuid.UUID | None) -> None:
    """Re-queue operations waiting for the one that just finished (e.g. a pre-empting delete)."""
    with session_scope(platform.session_factory) as s:
        waiting = waiting_operations(s, cluster_id)
        if waiting:
            s.execute(
                update(Operation)
                .where(Operation.id.in_([uuid.UUID(i) for i in waiting]))
                .values(last_enqueued_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )
    for operation_id in waiting:
        platform.queue.enqueue(operation_id)
