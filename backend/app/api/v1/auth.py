from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_platform, get_principal, get_session
from app.api.v1.schemas import (
    AuthResponse,
    LoginRequest,
    MembershipOut,
    MeResponse,
    Message,
    OrganizationOut,
    SignupRequest,
    SwitchOrganizationRequest,
    UserOut,
)
from app.application.auth_service import AuthResult, AuthService
from app.application.platform import Platform
from app.application.principal import Principal
from app.domain.rbac import permissions_for
from app.models import User
from app.repositories import queries

router = APIRouter(prefix="/auth", tags=["auth"])


def _respond(result: AuthResult, response: Response, platform: Platform) -> AuthResponse:
    settings = platform.settings
    response.set_cookie(
        key=settings.session_cookie_name,
        value=result.access_token,
        max_age=settings.access_token_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return AuthResponse(
        access_token=result.access_token,
        expires_at=result.expires_at,
        user=UserOut(id=result.user.id, email=result.user.email, name=result.user.name),
        organization=OrganizationOut(
            id=result.organization.id, name=result.organization.name, slug=result.organization.slug
        ),
        role=result.role.value,
    )


@router.post("/login", response_model=AuthResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> AuthResponse:
    result = AuthService(session, platform).login(body.email, body.password, body.organization_id, client_ip(request))
    return _respond(result, response, platform)


@router.post("/signup", response_model=AuthResponse, status_code=201)
def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> AuthResponse:
    result = AuthService(session, platform).signup(
        body.name, body.email, body.password, body.organization_name, client_ip(request)
    )
    return _respond(result, response, platform)


@router.post("/logout", response_model=Message)
def logout(response: Response, platform: Platform = Depends(get_platform)) -> Message:
    response.delete_cookie(platform.settings.session_cookie_name, path="/")
    return Message(message="Logged out.")


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_principal), session: Session = Depends(get_session)) -> MeResponse:
    user = session.get(User, principal.user_id)
    assert user is not None
    org = queries.organization(session, principal.organization_id)
    return MeResponse(
        user=UserOut(id=user.id, email=user.email, name=user.name),
        organization=OrganizationOut(id=org.id, name=org.name, slug=org.slug),
        role=principal.role.value,
        permissions=permissions_for(principal.role),
        organizations=[
            MembershipOut(organization_id=m.organization_id, organization_name=m.organization.name, role=m.role)
            for m in queries.memberships_of(session, user.id)
        ],
    )


@router.post("/switch-organization", response_model=AuthResponse)
def switch_organization(
    body: SwitchOrganizationRequest,
    response: Response,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> AuthResponse:
    result = AuthService(session, platform).switch_organization(principal, body.organization_id)
    return _respond(result, response, platform)
