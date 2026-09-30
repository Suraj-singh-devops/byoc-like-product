from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session, object_session

from app.application.audit_service import record_audit
from app.application.platform import Platform
from app.application.principal import Principal
from app.domain.enums import AuditEvent, AuditStatus
from app.domain.errors import Conflict, ValidationFailed
from app.domain.identities import organization_key
from app.domain.rbac import Permission
from app.domain.states import CloudAccountStatus
from app.domain.text import plural
from app.models import CloudAccount, Organization
from app.providers.cloud.base import (
    AccountRegistration,
    CloudAccountContext,
    MachineType,
    Region,
    StorageType,
)
from app.repositories import queries


def _field_error(field: str, message: str) -> ValidationFailed:
    return ValidationFailed(message, details={"fields": {field: message}})


def build_account_context(account: CloudAccount) -> CloudAccountContext:
    """The account as providers see it. Must be called with an account attached to a session: the
    organization's external ID (AWS, docs/adr/0014) is read from the organization row."""
    session = object_session(account)
    organization = session.get(Organization, account.organization_id) if session is not None else None
    return CloudAccountContext(
        account_id=str(account.id),
        organization_id=str(account.organization_id),
        provider=account.provider,
        project_id=account.project_id,
        auth_type=account.auth_type,
        service_account_email=account.service_account_email,
        region=account.region,
        role_arn=account.role_arn,
        external_id=organization.external_id if organization else None,
    )


class CloudAccountService:
    def __init__(self, session: Session, platform: Platform, principal: Principal) -> None:
        self.session = session
        self.platform = platform
        self.principal = principal

    def list(self) -> tuple[Sequence[CloudAccount], dict]:
        self.principal.require(Permission.CLOUD_ACCOUNT_READ)
        org = self.principal.organization_id
        return queries.cloud_accounts(self.session, org), queries.cluster_counts_by_account(self.session, org)

    def get(self, account_id: str) -> CloudAccount:
        self.principal.require(Permission.CLOUD_ACCOUNT_READ)
        return queries.cloud_account(self.session, self.principal.organization_id, account_id)

    def create(
        self,
        *,
        name: str,
        provider: str,
        project_id: str,
        region: str,
        service_account_email: str | None,
        role_arn: str | None,
        validate_now: bool,
    ) -> CloudAccount:
        self.principal.require(Permission.CLOUD_ACCOUNT_MANAGE)
        cloud = self.platform.registry.descriptor(provider)
        name = name.strip()
        if not 2 <= len(name) <= 100:
            raise _field_error("name", "Name must be 2-100 characters.")
        registration = cloud.check_account(
            AccountRegistration(project_id=project_id, service_account_email=service_account_email, role_arn=role_arn)
        )
        region = region.strip()
        known = {r.name for r in cloud.list_regions()}
        if region not in known:
            raise _field_error("region", f"'{region}' is not an available region.")
        if any(a.name == name for a in queries.cloud_accounts(self.session, self.principal.organization_id)):
            raise Conflict(f"A cloud account named '{name}' already exists.")

        account = CloudAccount(
            organization_id=self.principal.organization_id,
            name=name,
            provider=cloud.name,
            project_id=registration.project_id,
            region=region,
            auth_type=cloud.auth_type,
            service_account_email=registration.service_account_email,
            role_arn=registration.role_arn,
            status=CloudAccountStatus.PENDING.value,
            created_by_id=self.principal.user_id,
        )
        self.session.add(account)
        self.session.flush()
        principal = (
            {"role_arn": registration.role_arn}
            if registration.role_arn
            else {"service_account": registration.service_account_email}
        )
        record_audit(
            self.session,
            organization_id=self.principal.organization_id,
            principal=self.principal,
            event=AuditEvent.CLOUD_ACCOUNT_CREATED,
            resource_type="cloud_account",
            resource_id=account.id,
            resource_name=name,
            status=AuditStatus.SUCCESS,
            details={"provider": cloud.name, "project_id": registration.project_id, "region": region, **principal},
        )
        self.session.commit()
        if validate_now:
            self._validate(account)
        return account

    def onboarding(self, provider: str) -> dict[str, Any]:
        """What the customer grants, for the console's instructions (docs/adr/0003, docs/adr/0014)."""
        self.principal.require(Permission.CLOUD_ACCOUNT_READ)
        cloud = self.platform.registry.descriptor(provider)
        organization = queries.organization(self.session, self.principal.organization_id)
        return {
            "provider": cloud.name,
            "auth_type": cloud.auth_type,
            **cloud.onboarding(organization_key(organization.id), organization.external_id),
        }

    def validate(self, account_id: str) -> CloudAccount:
        self.principal.require(Permission.CLOUD_ACCOUNT_MANAGE)
        account = queries.cloud_account(self.session, self.principal.organization_id, account_id)
        self._validate(account)
        return account

    def _validate(self, account: CloudAccount) -> None:
        """Validation reaches into the customer account, so it runs in the terraform-runner as the
        identity that will provision (docs/adr/0003, docs/adr/0004). The account shows VALIDATING
        until the runner records the result and the CLOUD_ACCOUNT_VALIDATED audit event."""
        account.status = CloudAccountStatus.VALIDATING.value
        self.session.commit()
        self.platform.tasks.submit(
            "validate_account",
            {"account_id": str(account.id)},
            organization_id=account.organization_id,
            requested_by_id=self.principal.user_id,
        )
        self.session.refresh(account)

    def delete(self, account_id: str) -> None:
        self.principal.require(Permission.CLOUD_ACCOUNT_MANAGE)
        org = self.principal.organization_id
        account = queries.cloud_account(self.session, org, account_id)
        in_use = queries.cluster_counts_by_account(self.session, org).get(account.id, 0)
        if in_use:
            raise Conflict(
                f"This cloud account is used by {plural(in_use, 'cluster')}.",
                suggested_action="Delete those clusters first.",
            )
        networks = queries.network_counts_by_account(self.session, org).get(account.id, 0)
        if networks:
            raise Conflict(
                f"This cloud account is used by {plural(networks, 'registered network')}.",
                suggested_action="Remove those networks from their environments first.",
            )
        record_audit(
            self.session,
            organization_id=account.organization_id,
            principal=self.principal,
            event=AuditEvent.CLOUD_ACCOUNT_DELETED,
            resource_type="cloud_account",
            resource_id=account.id,
            resource_name=account.name,
            status=AuditStatus.SUCCESS,
            details={"project_id": account.project_id},
        )
        self.session.delete(account)
        self.session.commit()

    # ---------------------------------------------------------------- catalog

    def regions(self, account_id: str) -> list[Region]:
        return self.platform.registry.descriptor(self.get(account_id).provider).list_regions()

    def machine_types(self, account_id: str, zone: str) -> list[MachineType]:
        return self.platform.registry.descriptor(self.get(account_id).provider).list_machine_types(zone)

    def storage_types(self, account_id: str) -> list[StorageType]:
        return self.platform.registry.descriptor(self.get(account_id).provider).storage_types()
