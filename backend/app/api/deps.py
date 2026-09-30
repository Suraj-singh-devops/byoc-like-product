from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.application.auth_service import AuthService
from app.application.platform import Platform
from app.application.principal import Principal
from app.domain.errors import AuthenticationFailed


def get_platform(request: Request) -> Platform:
    return request.app.state.platform


def get_session(request: Request) -> Iterator[Session]:
    session = request.app.state.platform.session_factory()
    try:
        yield session
    finally:
        session.close()


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and request.app.state.platform.settings.trust_proxy_headers:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def _token(request: Request, cookie_name: str) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(cookie_name)


def get_principal(
    request: Request,
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> Principal:
    token = _token(request, platform.settings.session_cookie_name)
    if not token:
        raise AuthenticationFailed("Authentication required.", suggested_action="Log in and try again.")
    principal = AuthService(session, platform).principal_from_token(token, client_ip(request))
    request.state.user_id = str(principal.user_id)
    return principal
