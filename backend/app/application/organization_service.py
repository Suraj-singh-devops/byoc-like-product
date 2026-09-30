from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.application.audit_service import record_audit
from app.application.auth_service import check_password_strength, normalize_email
from app.application.principal import Principal
from app.domain.enums import AuditEvent, AuditStatus, Role
from app.domain.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.domain.rbac import Permission, can_change_membership
from app.infrastructure.security import hash_password
from app.models import OrganizationMember, User
from app.repositories import queries


def _parse_role(value: str) -> Role:
    try:
        return Role(value.upper())
    except ValueError as exc:
        raise ValidationFailed(
            f"Unknown role '{value}'.", details={"fields": {"role": "One of OWNER, ADMIN, OPERATOR, VIEWER."}}
        ) from exc


class OrganizationService:
    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    def members(self) -> Sequence[OrganizationMember]:
        self.principal.require(Permission.MEMBER_READ)
        return queries.members_of(self.session, self.principal.organization_id)

    def _deny(self) -> PermissionDenied:
        return PermissionDenied(
            "You cannot make this membership change.",
            reason="Only Owners can grant the Owner role or change another Owner.",
        )

    def add_member(self, email: str, name: str | None, role_value: str, password: str | None) -> OrganizationMember:
        self.principal.require(Permission.MEMBER_MANAGE)
        role = _parse_role(role_value)
        if not can_change_membership(self.principal.role, None, role):
            raise self._deny()
        email = normalize_email(email)
        org_id = self.principal.organization_id
        user = queries.user_by_email(self.session, email)
        if user is None:
            if not password:
                raise ValidationFailed(
                    "An initial password is required for a new user.",
                    details={"fields": {"password": "Required for new users."}},
                )
            check_password_strength(password)
            user = User(email=email, name=(name or email.split("@")[0]).strip(), password_hash=hash_password(password))
            self.session.add(user)
            self.session.flush()
        elif queries.membership(self.session, org_id, user.id) is not None:
            raise Conflict(f"{email} is already a member of this organization.")
        member = OrganizationMember(organization_id=org_id, user_id=user.id, role=role.value)
        self.session.add(member)
        self.session.flush()
        record_audit(
            self.session,
            organization_id=org_id,
            principal=self.principal,
            event=AuditEvent.MEMBER_ADDED,
            resource_type="member",
            resource_id=user.id,
            resource_name=email,
            status=AuditStatus.SUCCESS,
            details={"role": role.value},
        )
        self.session.commit()
        self.session.refresh(member)
        return member

    def _member(self, user_id: uuid.UUID) -> OrganizationMember:
        member = queries.membership(self.session, self.principal.organization_id, user_id)
        if member is None:
            raise NotFound("Member not found.")
        return member

    def _ensure_owner_remains(self, member: OrganizationMember) -> None:
        if member.role == Role.OWNER.value and queries.owner_count(self.session, member.organization_id) <= 1:
            raise Conflict(
                "An organization must keep at least one Owner.",
                suggested_action="Make another member an Owner first.",
            )

    def update_role(self, user_id: uuid.UUID, role_value: str) -> OrganizationMember:
        self.principal.require(Permission.MEMBER_MANAGE)
        role = _parse_role(role_value)
        member = self._member(user_id)
        current = Role(member.role)
        if not can_change_membership(self.principal.role, current, role):
            raise self._deny()
        if current == role:
            return member
        if current == Role.OWNER:
            self._ensure_owner_remains(member)
        member.role = role.value
        record_audit(
            self.session,
            organization_id=member.organization_id,
            principal=self.principal,
            event=AuditEvent.MEMBER_ROLE_CHANGED,
            resource_type="member",
            resource_id=member.user_id,
            resource_name=member.user.email,
            status=AuditStatus.SUCCESS,
            details={"from": current.value, "to": role.value},
        )
        self.session.commit()
        return member

    def remove_member(self, user_id: uuid.UUID) -> None:
        self.principal.require(Permission.MEMBER_MANAGE)
        member = self._member(user_id)
        if not can_change_membership(self.principal.role, Role(member.role), None):
            raise self._deny()
        self._ensure_owner_remains(member)
        email = member.user.email
        self.session.delete(member)
        record_audit(
            self.session,
            organization_id=self.principal.organization_id,
            principal=self.principal,
            event=AuditEvent.MEMBER_REMOVED,
            resource_type="member",
            resource_id=user_id,
            resource_name=email,
            status=AuditStatus.SUCCESS,
            details={"role": member.role},
        )
        self.session.commit()
