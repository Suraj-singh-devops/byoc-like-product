from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_principal, get_session
from app.api.v1.schemas import AuditLogOut, Page
from app.application.principal import Principal
from app.domain.rbac import Permission
from app.repositories import queries

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut])
def list_audit_logs(
    action: str | None = None,
    status: str | None = None,
    user: str | None = Query(default=None, max_length=320),
    resource_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> Page[AuditLogOut]:
    principal.require(Permission.AUDIT_READ)
    stmt = queries.audit_query(
        principal.organization_id,
        action=action.upper() if action else None,
        status=status.upper() if status else None,
        user=user,
        resource_id=resource_id,
    )
    items, total = queries.paginate(session, stmt, limit, offset)
    return Page[AuditLogOut](
        items=[AuditLogOut.build(e, principal.organization_name) for e in items],
        total=total,
        limit=limit,
        offset=offset,
    )
