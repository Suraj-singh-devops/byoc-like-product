from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_principal, get_session
from app.api.v1.schemas import EventOut
from app.application.principal import Principal
from app.domain.rbac import Permission
from app.repositories import queries

router = APIRouter(tags=["clusters"])


@router.get("/events", response_model=list[EventOut])
def recent_events(
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[EventOut]:
    principal.require(Permission.CLUSTER_READ)
    events = queries.org_events(session, principal.organization_id, limit)
    names = queries.cluster_names(session, {e.cluster_id for e in events})
    return [EventOut.build(e, names.get(e.cluster_id)) for e in events]
