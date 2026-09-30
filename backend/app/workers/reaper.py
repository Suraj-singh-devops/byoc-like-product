"""Recovers operations whose queue message was lost or whose worker died."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from app.application.platform import Platform
from app.application.provisioning.outcome import finalize_operation
from app.application.provisioning.runner import wake_waiting
from app.domain.errors import ProvisioningError
from app.domain.states import RUNNING_OPERATION_STATUSES, OperationStatus
from app.infrastructure.db import session_scope
from app.infrastructure.logging import get_logger
from app.models import Operation

log = get_logger(__name__)

REENQUEUE_AFTER = timedelta(seconds=120)


class OperationReaper:
    def __init__(self, platform: Platform) -> None:
        self.platform = platform

    def tick(self) -> dict[str, int]:
        settings = self.platform.settings
        now = datetime.now(UTC)
        requeue: list[str] = []
        finished_clusters: set[uuid.UUID] = set()
        failed = cancelled = 0
        with session_scope(self.platform.session_factory) as s:
            pending = s.scalars(
                select(Operation).where(
                    Operation.status == OperationStatus.PENDING.value,
                    Operation.cancel_requested.is_(False),
                    or_(Operation.last_enqueued_at.is_(None), Operation.last_enqueued_at < now - REENQUEUE_AFTER),
                )
            ).all()
            for op in pending:
                op.last_enqueued_at = now
                # Still waiting for a free worker slot: re-enqueueing would only add duplicates.
                if not self.platform.queue.contains(str(op.id)):
                    requeue.append(str(op.id))

            lease_cutoff = now - timedelta(seconds=settings.operation_lease_seconds)
            abandoned = s.scalars(
                select(Operation).where(
                    Operation.status.in_([st.value for st in RUNNING_OPERATION_STATUSES]),
                    or_(Operation.heartbeat_at.is_(None), Operation.heartbeat_at < lease_cutoff),
                )
            ).all()
            for op in abandoned:
                log.warning("operation_abandoned", operation_id=str(op.id), worker_id=op.worker_id, attempt=op.attempt)
                if op.cancel_requested:
                    finalize_operation(s, op, OperationStatus.CANCELLED)
                    cancelled += 1
                    if op.cluster_id is not None:
                        finished_clusters.add(op.cluster_id)
                elif op.attempt >= settings.max_operation_attempts:
                    finalize_operation(
                        s,
                        op,
                        OperationStatus.FAILED,
                        error=ProvisioningError(
                            "The operation stopped responding too many times.",
                            code="OPERATION_ABANDONED",
                            reason=f"{op.attempt} attempts were interrupted before finishing.",
                            suggested_action="Check the worker logs, then retry the operation.",
                        ),
                    )
                    failed += 1
                    if op.cluster_id is not None:
                        finished_clusters.add(op.cluster_id)
                else:
                    metadata = dict(op.metadata_ or {})
                    metadata["log"] = [
                        *(metadata.get("log") or []),
                        {"at": now.isoformat(), "step": None, "message": "Worker stopped responding; re-queued"},
                    ]
                    op.metadata_ = metadata
                    op.status = OperationStatus.PENDING.value
                    op.worker_id = None
                    op.last_enqueued_at = now
                    requeue.append(str(op.id))
        for operation_id in requeue:
            self.platform.queue.enqueue(operation_id)
        for cluster_id in finished_clusters:
            wake_waiting(self.platform, cluster_id)
        if requeue or failed or cancelled:
            log.info("reaper_tick", requeued=len(requeue), failed=failed, cancelled=cancelled)
        return {"requeued": len(requeue), "failed": failed, "cancelled": cancelled}
