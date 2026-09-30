from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_platform, get_principal, get_session
from app.api.v1.schemas import OperationOut, Page
from app.application.operation_service import OperationService
from app.application.platform import Platform
from app.application.principal import Principal
from app.models import Operation
from app.repositories import queries

router = APIRouter(prefix="/operations", tags=["operations"])


def _build(session: Session, ops: list[Operation], *, with_log: bool) -> list[OperationOut]:
    names = queries.cluster_names(session, {o.cluster_id for o in ops})
    emails = queries.emails_by_user_id(session, {o.created_by_id for o in ops if o.created_by_id})
    return [
        OperationOut.build(
            o,
            names.get(o.cluster_id) if o.cluster_id else None,
            emails.get(o.created_by_id) if o.created_by_id else None,
            with_log=with_log,
        )
        for o in ops
    ]


@router.get("", response_model=Page[OperationOut])
def list_operations(
    cluster_id: str | None = None,
    status: str | None = None,
    operation_type: str | None = Query(default=None, alias="type"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> Page[OperationOut]:
    items, total = OperationService(session, platform, principal).list(
        cluster_id=cluster_id, status=status, operation_type=operation_type, limit=limit, offset=offset
    )
    return Page[OperationOut](items=_build(session, items, with_log=False), total=total, limit=limit, offset=offset)


@router.get("/{operation_id}", response_model=OperationOut)
def get_operation(
    operation_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> OperationOut:
    op = OperationService(session, platform, principal).get(operation_id)
    return _build(session, [op], with_log=True)[0]


@router.post("/{operation_id}/cancel", response_model=OperationOut)
def cancel_operation(
    operation_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> OperationOut:
    op = OperationService(session, platform, principal).cancel(operation_id)
    return _build(session, [op], with_log=True)[0]


@router.post("/{operation_id}/retry", response_model=OperationOut, status_code=202)
def retry_operation(
    operation_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> OperationOut:
    op = OperationService(session, platform, principal).retry(operation_id)
    return _build(session, [op], with_log=True)[0]
