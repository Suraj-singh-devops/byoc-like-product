from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.audit_service import record_audit
from app.application.platform import Platform
from app.application.preconditions import require_available_network, require_connected_account
from app.application.principal import Principal
from app.application.provisioning.outcome import finalize_operation, request_cancellation
from app.domain.enums import (
    MUTATING_OPERATION_TYPES,
    OPERATION_STARTED_EVENTS,
    AuditEvent,
    AuditStatus,
    OperationType,
)
from app.domain.errors import Conflict
from app.domain.rbac import Permission
from app.domain.states import (
    TERMINAL_OPERATION_STATUSES,
    ClusterLifecycle,
    OperationStatus,
    assert_cluster_transition,
)
from app.models import Cluster, Operation
from app.repositories import queries


def request_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def find_idempotent_operation(
    session: Session,
    organization_id: uuid.UUID,
    idempotency_key: str | None,
    operation_type: OperationType,
    request_hash: str,
) -> Operation | None:
    """Return the operation previously created with this key, if the request is the same."""
    if not idempotency_key:
        return None
    existing = (
        session.query(Operation).filter_by(organization_id=organization_id, idempotency_key=idempotency_key).first()
    )
    if existing is None:
        return None
    if existing.operation_type != operation_type or (existing.metadata_ or {}).get("request_hash") != request_hash:
        raise Conflict(
            "This Idempotency-Key was already used for a different request.",
            code="IDEMPOTENCY_KEY_REUSED",
            suggested_action="Use a new Idempotency-Key for a new request.",
        )
    return existing


def new_operation(
    session: Session,
    *,
    principal: Principal,
    cluster: Cluster,
    operation_type: OperationType,
    params: dict[str, Any],
    idempotency_key: str | None = None,
    request_hash: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> Operation:
    op = Operation(
        organization_id=principal.organization_id,
        cluster_id=cluster.id,
        operation_type=operation_type.value,
        status=OperationStatus.PENDING.value,
        metadata_={"params": params, "request_hash": request_hash, "steps": [], "log": [], **(extra_metadata or {})},
        idempotency_key=idempotency_key,
        created_by_id=principal.user_id,
        last_enqueued_at=datetime.now(UTC),
    )
    session.add(op)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict(
            "Another operation is already in progress for this cluster.",
            suggested_action="Wait for it to finish, or cancel it, and try again.",
        ) from exc
    return op


def audit_started(session: Session, principal: Principal, cluster: Cluster, op: Operation) -> None:
    record_audit(
        session,
        organization_id=principal.organization_id,
        principal=principal,
        event=OPERATION_STARTED_EVENTS[OperationType(op.operation_type)],
        resource_type="cluster",
        resource_id=cluster.id,
        resource_name=cluster.name,
        status=AuditStatus.SUCCESS,
        details={"operation_type": op.operation_type, "params": (op.metadata_ or {}).get("params", {})},
        operation_id=op.id,
    )


class OperationService:
    def __init__(self, session: Session, platform: Platform, principal: Principal) -> None:
        self.session = session
        self.platform = platform
        self.principal = principal

    def list(
        self,
        *,
        cluster_id: str | None,
        status: str | None,
        operation_type: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Operation], int]:
        self.principal.require(Permission.OPERATION_READ)
        cluster_uuid = None
        if cluster_id:
            cluster_uuid = queries.cluster(
                self.session, self.principal.organization_id, cluster_id, include_deleted=True
            ).id
        stmt = queries.operations_query(
            self.principal.organization_id,
            cluster_id=cluster_uuid,
            status=status.upper() if status else None,
            operation_type=operation_type.upper() if operation_type else None,
        )
        return queries.paginate(self.session, stmt, limit, offset)

    def get(self, operation_id: str) -> Operation:
        self.principal.require(Permission.OPERATION_READ)
        return queries.operation(self.session, self.principal.organization_id, operation_id)

    def _cluster_for(self, op: Operation) -> Cluster:
        assert op.cluster_id is not None
        return queries.cluster(
            self.session, self.principal.organization_id, op.cluster_id, include_deleted=True, for_update=True
        )

    def cancel(self, operation_id: str) -> Operation:
        op = self.get(operation_id)
        kind = OperationType(op.operation_type)
        self.principal.require_operation(kind)
        cluster = self._cluster_for(op)
        if OperationStatus(op.status) in TERMINAL_OPERATION_STATUSES:
            raise Conflict(f"The operation already finished ({op.status}).")
        if kind == OperationType.DELETE_CLUSTER:
            raise Conflict(
                "A deletion cannot be cancelled once it has been confirmed.",
                code="DELETE_NOT_CANCELLABLE",
                suggested_action="If the deletion fails, retry it to finish removing the cluster.",
            )
        reason = f"Cancelled by {self.principal.email}"
        if op.status == OperationStatus.PENDING:
            request_cancellation(op, by=self.principal, reason=reason)
            finalize_operation(self.session, op, OperationStatus.CANCELLED, actor=self.principal)
        else:
            request_cancellation(op, by=self.principal, reason=reason)
            record_audit(
                self.session,
                organization_id=self.principal.organization_id,
                principal=self.principal,
                event=AuditEvent.OPERATION_CANCEL_REQUESTED,
                resource_type="cluster",
                resource_id=cluster.id,
                resource_name=cluster.name,
                status=AuditStatus.SUCCESS,
                details={"operation_type": op.operation_type},
                operation_id=op.id,
            )
        self.session.commit()
        return op

    def retry(self, operation_id: str) -> Operation:
        op = self.get(operation_id)
        kind = OperationType(op.operation_type)
        self.principal.require_operation(kind)
        cluster = self._cluster_for(op)
        if op.status not in (OperationStatus.FAILED, OperationStatus.CANCELLED):
            raise Conflict("Only failed or cancelled operations can be retried.")
        if cluster.deleted_at is not None:
            raise Conflict("The cluster has been deleted.")
        if kind in MUTATING_OPERATION_TYPES:
            latest = queries.operations_query(self.principal.organization_id, cluster_id=cluster.id)
            newer = [
                o
                for o in self.session.scalars(latest).all()
                if o.created_at > op.created_at and OperationType(o.operation_type) in MUTATING_OPERATION_TYPES
            ]
            if newer:
                raise Conflict("A newer operation has run on this cluster since; this one can no longer be retried.")

        if kind in (OperationType.CREATE_CLUSTER, OperationType.SCALE_CLUSTER):
            require_connected_account(self.session, cluster)
            require_available_network(self.session, cluster)
        params = dict((op.metadata_ or {}).get("params", {}))
        lifecycle = ClusterLifecycle(cluster.lifecycle_state)
        if kind == OperationType.CREATE_CLUSTER:
            if lifecycle != ClusterLifecycle.FAILED:
                raise Conflict(f"The cluster is {lifecycle}; only a failed cluster can be re-provisioned.")
            target = ClusterLifecycle.CREATING
        elif kind == OperationType.SCALE_CLUSTER:
            if int(params.get("to", 0)) <= int(params.get("from", 0)):
                raise Conflict("Scaling down is not supported.", code="SCALE_DOWN_NOT_SUPPORTED")
            if lifecycle != ClusterLifecycle.ACTIVE:
                raise Conflict(f"The cluster is {lifecycle} and cannot be scaled now.")
            target = ClusterLifecycle.SCALING
        elif kind == OperationType.DELETE_CLUSTER:
            target = ClusterLifecycle.DELETING
        elif kind == OperationType.HEALTH_CHECK:
            if lifecycle not in (ClusterLifecycle.ACTIVE, ClusterLifecycle.SCALING):
                raise Conflict(f"The cluster is {lifecycle}; health checks run on active clusters.")
            target = lifecycle
        else:
            raise Conflict(f"{kind} operations cannot be retried.")
        assert_cluster_transition(lifecycle, target)
        cluster.lifecycle_state = target.value
        cluster.status_message = None
        new_op = new_operation(
            self.session,
            principal=self.principal,
            cluster=cluster,
            operation_type=kind,
            params=params,
            extra_metadata={"retry_of": str(op.id)},
        )
        record_audit(
            self.session,
            organization_id=self.principal.organization_id,
            principal=self.principal,
            event=AuditEvent.OPERATION_RETRIED,
            resource_type="cluster",
            resource_id=cluster.id,
            resource_name=cluster.name,
            status=AuditStatus.SUCCESS,
            details={"retry_of": str(op.id), "operation_type": kind.value},
            operation_id=new_op.id,
        )
        self.session.commit()
        self.platform.queue.enqueue(str(new_op.id))
        return new_op
