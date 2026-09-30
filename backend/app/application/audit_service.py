from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.application.principal import Principal
from app.domain.enums import AuditEvent, AuditStatus
from app.infrastructure.logging import get_logger
from app.models import AuditLog

log = get_logger(__name__)


def record_audit(
    session: Session,
    *,
    organization_id: uuid.UUID,
    event: AuditEvent,
    resource_type: str,
    status: AuditStatus,
    principal: Principal | None = None,
    user_id: uuid.UUID | None = None,
    user_email: str | None = None,
    resource_id: uuid.UUID | str | None = None,
    resource_name: str | None = None,
    details: dict[str, Any] | None = None,
    operation_id: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Add an audit entry to the session; it is committed with the caller's transaction.

    ``details`` must never contain secrets (TRD §35).
    """
    entry = AuditLog(
        organization_id=organization_id,
        user_id=principal.user_id if principal else user_id,
        user_email=principal.email if principal else user_email,
        action=event.value,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        resource_name=resource_name,
        status=status.value,
        details=details or {},
        operation_id=operation_id,
        ip_address=(principal.ip_address if principal else None) or ip_address,
    )
    session.add(entry)
    log.info(
        "audit",
        audit_event=entry.action,
        status=entry.status,
        user=entry.user_email,
        organization_id=str(organization_id),
        resource_type=resource_type,
        resource_id=entry.resource_id,
        operation_id=str(operation_id) if operation_id else None,
    )
    return entry
