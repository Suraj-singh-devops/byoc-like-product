from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import get_principal, get_session
from app.api.v1.schemas import MemberCreateRequest, MemberOut, MemberUpdateRequest, OrganizationOut
from app.application.organization_service import OrganizationService
from app.application.principal import Principal
from app.models import OrganizationMember
from app.repositories import queries

router = APIRouter(prefix="/organizations/current", tags=["organizations"])


def _member(m: OrganizationMember) -> MemberOut:
    return MemberOut(user_id=m.user_id, email=m.user.email, name=m.user.name, role=m.role, joined_at=m.created_at)


@router.get("", response_model=OrganizationOut)
def current_organization(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_session)
) -> OrganizationOut:
    org = queries.organization(session, principal.organization_id)
    return OrganizationOut(id=org.id, name=org.name, slug=org.slug)


@router.get("/members", response_model=list[MemberOut])
def list_members(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_session)
) -> list[MemberOut]:
    return [_member(m) for m in OrganizationService(session, principal).members()]


@router.post("/members", response_model=MemberOut, status_code=201)
def add_member(
    body: MemberCreateRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> MemberOut:
    member = OrganizationService(session, principal).add_member(body.email, body.name, body.role, body.password)
    return _member(member)


@router.patch("/members/{user_id}", response_model=MemberOut)
def update_member(
    user_id: uuid.UUID,
    body: MemberUpdateRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> MemberOut:
    return _member(OrganizationService(session, principal).update_role(user_id, body.role))


@router.delete("/members/{user_id}", status_code=204)
def remove_member(
    user_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> Response:
    OrganizationService(session, principal).remove_member(user_id)
    return Response(status_code=204)
