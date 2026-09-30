from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit_service import record_audit
from app.application.platform import Platform
from app.application.principal import Principal
from app.domain.enums import AuditEvent, AuditStatus, Role
from app.domain.errors import AuthenticationFailed, Conflict, PermissionDenied, RateLimited, ValidationFailed
from app.infrastructure.security import (
    MIN_PASSWORD_LENGTH,
    burn_password_check,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models import Organization, OrganizationMember, User
from app.repositories import queries

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
INVALID_LOGIN = "Invalid email or password."


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if not EMAIL_RE.match(value) or len(value) > 320:
        raise ValidationFailed("Enter a valid email address.", details={"fields": {"email": "Invalid email."}})
    return value


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationFailed(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
            details={"fields": {"password": f"At least {MIN_PASSWORD_LENGTH} characters."}},
        )


def unique_slug(session: Session, name: str) -> str:
    base = _SLUG_RE.sub("-", name.lower()).strip("-")[:40] or "org"
    slug = base
    while session.scalar(select(Organization.id).where(Organization.slug == slug)) is not None:
        slug = f"{base}-{secrets.token_hex(2)}"
    return slug


@dataclass
class AuthResult:
    access_token: str
    expires_at: datetime
    user: User
    organization: Organization
    role: Role


class AuthService:
    def __init__(self, session: Session, platform: Platform) -> None:
        self.session = session
        self.platform = platform
        self.settings = platform.settings

    def _issue(self, user: User, member: OrganizationMember) -> AuthResult:
        token, expires_at = create_access_token(
            secret=self.settings.secret_key,
            user_id=user.id,
            organization_id=member.organization_id,
            ttl_minutes=self.settings.access_token_ttl_minutes,
        )
        return AuthResult(token, expires_at, user, member.organization, Role(member.role))

    def _rate_limit(self, key: str) -> None:
        limiter = self.platform.rate_limiter
        if not limiter.hit(key, self.settings.login_rate_limit_attempts, self.settings.login_rate_limit_window_seconds):
            raise RateLimited(
                "Too many login attempts.",
                suggested_action="Wait a few minutes and try again.",
            )

    def login(self, email: str, password: str, organization_id: uuid.UUID | None, ip: str | None) -> AuthResult:
        email_key = email.strip().lower()
        self._rate_limit(f"login:ip:{ip or 'unknown'}")
        self._rate_limit(f"login:email:{email_key}")
        user = queries.user_by_email(self.session, email_key)
        if user is None:
            burn_password_check(password)
            raise AuthenticationFailed(INVALID_LOGIN)
        members = queries.memberships_of(self.session, user.id)
        if not user.is_active or not verify_password(user.password_hash, password):
            if members:
                record_audit(
                    self.session,
                    organization_id=members[0].organization_id,
                    event=AuditEvent.LOGIN_FAILED,
                    resource_type="user",
                    resource_id=user.id,
                    resource_name=user.email,
                    status=AuditStatus.FAILURE,
                    user_id=user.id,
                    user_email=user.email,
                    details={"reason": "invalid_credentials"},
                    ip_address=ip,
                )
                self.session.commit()
            raise AuthenticationFailed(INVALID_LOGIN)
        if not members:
            raise AuthenticationFailed(
                "Your account does not belong to any organization.",
                suggested_action="Ask an organization Owner or Admin to add you.",
            )
        member = next((m for m in members if m.organization_id == organization_id), members[0])
        user.last_login_at = datetime.now(UTC)
        record_audit(
            self.session,
            organization_id=member.organization_id,
            event=AuditEvent.LOGIN_SUCCEEDED,
            resource_type="user",
            resource_id=user.id,
            resource_name=user.email,
            status=AuditStatus.SUCCESS,
            user_id=user.id,
            user_email=user.email,
            ip_address=ip,
        )
        self.session.commit()
        self.platform.rate_limiter.reset(f"login:email:{email_key}")
        return self._issue(user, member)

    def signup(self, name: str, email: str, password: str, organization_name: str, ip: str | None) -> AuthResult:
        if not self.settings.allow_signup:
            raise PermissionDenied("Self-service sign-up is disabled.", suggested_action="Ask an administrator.")
        self._rate_limit(f"signup:ip:{ip or 'unknown'}")
        email = normalize_email(email)
        check_password_strength(password)
        if not name.strip() or not organization_name.strip():
            raise ValidationFailed("Name and organization name are required.")
        if queries.user_by_email(self.session, email) is not None:
            raise Conflict(
                "An account with this email already exists.",
                suggested_action="Log in instead, or ask an Owner/Admin to add you to their organization.",
            )
        org = Organization(name=organization_name.strip(), slug=unique_slug(self.session, organization_name))
        user = User(email=email, name=name.strip(), password_hash=hash_password(password))
        self.session.add_all([org, user])
        self.session.flush()
        member = OrganizationMember(organization_id=org.id, user_id=user.id, role=Role.OWNER.value)
        self.session.add(member)
        self.session.flush()
        record_audit(
            self.session,
            organization_id=org.id,
            event=AuditEvent.USER_SIGNED_UP,
            resource_type="organization",
            resource_id=org.id,
            resource_name=org.name,
            status=AuditStatus.SUCCESS,
            user_id=user.id,
            user_email=user.email,
            ip_address=ip,
        )
        self.session.commit()
        self.session.refresh(member)
        return self._issue(user, member)

    def switch_organization(self, principal: Principal, organization_id: uuid.UUID) -> AuthResult:
        member = queries.membership(self.session, organization_id, principal.user_id)
        if member is None:
            raise PermissionDenied("You are not a member of that organization.")
        user = self.session.get(User, principal.user_id)
        assert user is not None
        record_audit(
            self.session,
            organization_id=organization_id,
            event=AuditEvent.ORGANIZATION_SWITCHED,
            resource_type="organization",
            resource_id=organization_id,
            resource_name=member.organization.name,
            status=AuditStatus.SUCCESS,
            user_id=user.id,
            user_email=user.email,
            ip_address=principal.ip_address,
        )
        self.session.commit()
        return self._issue(user, member)

    def principal_from_token(self, token: str, ip: str | None) -> Principal:
        claims = decode_access_token(token, secret=self.settings.secret_key)
        try:
            user_id = uuid.UUID(claims["sub"])
            organization_id = uuid.UUID(claims["org"])
        except (KeyError, ValueError) as exc:
            raise AuthenticationFailed("Invalid access token.") from exc
        user = self.session.get(User, user_id)
        if user is None or not user.is_active:
            raise AuthenticationFailed("Your account is disabled or no longer exists.")
        # Membership and role are read on every request, so removals and role changes apply at once.
        member = queries.membership(self.session, organization_id, user_id)
        if member is None:
            raise AuthenticationFailed(
                "You are no longer a member of this organization.", suggested_action="Log in again."
            )
        return Principal(
            user_id=user.id,
            email=user.email,
            name=user.name,
            organization_id=organization_id,
            organization_name=member.organization.name,
            role=Role(member.role),
            ip_address=ip,
        )
