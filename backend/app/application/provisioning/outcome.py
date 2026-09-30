"""Finishing an operation: its final status, the cluster's resulting lifecycle, audit and events.

Shared by the workflow runner, the reaper (abandoned operations) and the API (cancellation,
delete pre-emption).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit_service import record_audit
from app.application.principal import Principal
from app.domain.enums import (
    OPERATION_COMPLETED_EVENTS,
    AuditEvent,
    AuditStatus,
    EventSeverity,
    OperationType,
)
from app.domain.errors import PlatformError
from app.domain.states import ClusterLifecycle, OperationStatus, lifecycle_change
from app.infrastructure.logging import get_logger
from app.infrastructure.metrics import OPERATION_DURATION, OPERATIONS, PROVISIONING_DURATION
from app.models import Cluster, ClusterEvent, Operation, User

log = get_logger(__name__)


def add_event(
    session: Session,
    cluster: Cluster,
    event_type: str,
    severity: EventSeverity,
    message: str,
    *,
    node_name: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        ClusterEvent(
            organization_id=cluster.organization_id,
            cluster_id=cluster.id,
            node_name=node_name,
            event_type=event_type,
            severity=severity.value,
            message=message,
            details=details or {},
        )
    )


def request_cancellation(op: Operation, *, by: Principal, reason: str) -> None:
    """Ask a running operation to stop at its next safe point."""
    op.cancel_requested = True
    metadata = dict(op.metadata_ or {})
    metadata["cancel"] = {"requested_by": by.email, "user_id": str(by.user_id), "reason": reason}
    metadata["log"] = [
        *(metadata.get("log") or []),
        {"at": datetime.now(UTC).isoformat(), "step": op.current_step, "message": f"Cancellation requested: {reason}"},
    ]
    op.metadata_ = metadata


_LABELS = {
    OperationType.CREATE_CLUSTER: ("CREATE", "Provisioning"),
    OperationType.SCALE_CLUSTER: ("SCALE", "Scaling"),
    OperationType.DELETE_CLUSTER: ("DELETE", "Deletion"),
}


def _cluster_outcome(
    session: Session, cluster: Cluster, op: Operation, status: OperationStatus, error: PlatformError | None
) -> None:
    """Lifecycle after a failed or cancelled operation. Completed workflows set it themselves."""
    kind = OperationType(op.operation_type)
    if status == OperationStatus.COMPLETED or kind not in _LABELS:
        return
    prefix, what = _LABELS[kind]
    cancelled = status == OperationStatus.CANCELLED
    if kind != OperationType.DELETE_CLUSTER and cluster.lifecycle_state == ClusterLifecycle.DELETING:
        add_event(
            session, cluster, f"{prefix}_CANCELLED", EventSeverity.INFO, f"{what} stopped: the cluster is being deleted"
        )
        return

    detail = error.message if error else "cancelled"
    if error and error.reason:
        detail += f" ({error.reason})"
    params = (op.metadata_ or {}).get("params", {})
    if kind == OperationType.CREATE_CLUSTER:
        expected, target = ClusterLifecycle.CREATING, ClusterLifecycle.FAILED
        message = (
            "Provisioning was cancelled. Retry the operation or delete the cluster."
            if cancelled
            else f"Provisioning failed: {detail}"
        )
        severity = EventSeverity.WARNING if cancelled else EventSeverity.CRITICAL
    elif kind == OperationType.SCALE_CLUSTER:
        expected, target = ClusterLifecycle.SCALING, ClusterLifecycle.ACTIVE
        verb = "was cancelled" if cancelled else "failed"
        message = (
            f"Scaling to {params.get('to')} nodes {verb}: {detail}. The cluster keeps running on its existing "
            "nodes; retry the operation to finish scaling."
        )
        severity = EventSeverity.INFO if cancelled else EventSeverity.WARNING
    else:
        expected, target = ClusterLifecycle.DELETING, ClusterLifecycle.FAILED
        message = f"Deletion failed: {detail}. Retry the operation to finish deleting the cluster."
        severity = EventSeverity.CRITICAL

    new = lifecycle_change(cluster.lifecycle_state, expected, target)
    if new is not None:
        cluster.lifecycle_state = new.value
        cluster.status_message = message
    add_event(session, cluster, f"{prefix}_{'CANCELLED' if cancelled else 'FAILED'}", severity, message)


def _audit(
    session: Session,
    cluster: Cluster,
    op: Operation,
    status: OperationStatus,
    error: PlatformError | None,
    actor: Principal | None,
) -> None:
    kind = OperationType(op.operation_type)
    metadata = op.metadata_ or {}
    details: dict[str, Any] = {"operation_type": kind.value, "params": metadata.get("params", {})}
    if status == OperationStatus.COMPLETED:
        event = OPERATION_COMPLETED_EVENTS.get(kind)
        if event is None:
            return
        audit_status = AuditStatus.SUCCESS
    elif status == OperationStatus.FAILED:
        event, audit_status = AuditEvent.OPERATION_FAILED, AuditStatus.FAILURE
        if error is not None:
            details["error"] = error.to_dict()
    else:
        event, audit_status = AuditEvent.OPERATION_CANCELLED, AuditStatus.SUCCESS
        details["reason"] = (metadata.get("cancel") or {}).get("reason")

    user_id, user_email = op.created_by_id, None
    cancel = metadata.get("cancel") or {}
    if actor is None and status == OperationStatus.CANCELLED and cancel.get("user_id"):
        user_id, user_email = uuid.UUID(cancel["user_id"]), cancel.get("requested_by")
    if actor is None and user_email is None and user_id is not None:
        user = session.get(User, user_id)
        user_email = user.email if user else None
    record_audit(
        session,
        organization_id=op.organization_id,
        event=event,
        principal=actor,
        user_id=user_id,
        user_email=user_email,
        resource_type="cluster",
        resource_id=cluster.id,
        resource_name=cluster.name,
        status=audit_status,
        details=details,
        operation_id=op.id,
    )


def finalize_operation(
    session: Session,
    op: Operation,
    status: OperationStatus,
    *,
    error: PlatformError | None = None,
    result: dict[str, Any] | None = None,
    actor: Principal | None = None,
) -> None:
    """Record the final status. ``actor`` is the user who caused it, when it was not the worker."""
    now = datetime.now(UTC)
    op.status = status.value
    op.completed_at = now
    op.cancel_requested = False
    if status == OperationStatus.COMPLETED:
        op.progress = 100
    metadata = dict(op.metadata_ or {})
    if result is not None:
        metadata["result"] = result
    if error is not None:
        metadata["error"] = error.to_dict()
        op.error_code = error.code
        op.error_message = error.message
    elif status == OperationStatus.CANCELLED:
        op.error_code = "CANCELLED"
        op.error_message = (metadata.get("cancel") or {}).get("reason") or "The operation was cancelled."
    op.metadata_ = metadata

    cluster = session.get(Cluster, op.cluster_id) if op.cluster_id else None
    if cluster is not None:
        _cluster_outcome(session, cluster, op, status, error)
        _audit(session, cluster, op, status, error, actor)

    result_label = status.value.lower()
    OPERATIONS.labels(op.operation_type, result_label).inc()
    if op.started_at is not None:
        OPERATION_DURATION.labels(op.operation_type, result_label).observe((now - op.started_at).total_seconds())
    if op.operation_type == OperationType.CREATE_CLUSTER and status == OperationStatus.COMPLETED:
        PROVISIONING_DURATION.observe((now - op.created_at).total_seconds())
    log.info(
        "operation_finished",
        operation_id=str(op.id),
        operation_type=op.operation_type,
        status=status.value,
        error_code=op.error_code,
    )


def waiting_operations(session: Session, cluster_id: uuid.UUID | None) -> list[str]:
    """PENDING operations of a cluster, e.g. a delete waiting for the operation it pre-empted."""
    if cluster_id is None:
        return []
    ids = session.scalars(
        select(Operation.id).where(
            Operation.cluster_id == cluster_id,
            Operation.status == OperationStatus.PENDING.value,
            Operation.cancel_requested.is_(False),
        )
    ).all()
    return [str(i) for i in ids]
