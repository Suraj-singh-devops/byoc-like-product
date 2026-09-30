"""Registered networks: platform records of a customer's existing VPC and subnets
(docs/adr/0013). The platform looks networks up in the customer's cloud; it never creates or
changes them."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.application.audit_service import record_audit
from app.application.platform import Platform
from app.application.principal import Principal
from app.domain.enums import AuditEvent, AuditStatus
from app.domain.errors import Conflict, NotFound, ValidationFailed
from app.domain.network import NetworkRef, SubnetRef
from app.domain.rbac import Permission
from app.domain.states import CloudAccountStatus, NetworkStatus
from app.domain.text import plural
from app.models import CloudAccount, Cluster, Network
from app.providers.cloud.base import NetworkDetails, NetworkLookup, SubnetInfo, ValidationCheck
from app.repositories import queries

# 2-63 lowercase letters, digits and hyphens, starting with a letter.
NETWORK_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")


def _field_error(field: str, message: str) -> ValidationFailed:
    return ValidationFailed(message, details={"fields": {field: message}})


def network_ref(network: Network) -> NetworkRef:
    """What a cluster copies into its desired state: resource paths, ranges and zones from the
    latest successful lookup, never from the client."""
    details = network.details or {}
    return NetworkRef(
        id=str(network.id),
        name=network.name,
        vpc=str(details["vpc"]),
        subnets=tuple(SubnetRef(id=str(s["id"]), cidr=str(s["cidr"]), zone=s.get("zone")) for s in details["subnets"]),
    )


class NetworkService:
    def __init__(self, session: Session, platform: Platform, principal: Principal) -> None:
        self.session = session
        self.platform = platform
        self.principal = principal

    # ------------------------------------------------------------------ reads

    def list(self, environment_id: str | None = None, provider: str | None = None) -> Sequence[Network]:
        self.principal.require(Permission.NETWORK_READ)
        org = self.principal.organization_id
        env = queries.environment(self.session, org, environment_id).id if environment_id else None
        return queries.networks(self.session, org, environment_id=env, provider=provider)

    def get(self, network_id: str) -> Network:
        self.principal.require(Permission.NETWORK_READ)
        return queries.network(self.session, self.principal.organization_id, network_id)

    def cluster_counts(self) -> dict[Any, int]:
        return queries.cluster_counts_by_network(self.session, self.principal.organization_id)

    # ---------------------------------------------------------------- lookups

    def _connected_account(self, cloud_account_id: str) -> CloudAccount:
        try:
            account = queries.cloud_account(self.session, self.principal.organization_id, cloud_account_id)
        except NotFound as exc:
            raise _field_error("cloud_account_id", "Unknown cloud account.") from exc
        if account.status != CloudAccountStatus.CONNECTED:
            raise ValidationFailed(
                f"Cloud account '{account.name}' is not connected ({account.status}).",
                suggested_action="Open Cloud accounts, fix any reported problem and validate the account again.",
                details={"fields": {"cloud_account_id": "Connect this cloud account first."}},
            )
        return account

    def _checked_lookup(self, account: CloudAccount, region: str, vpc: str, subnets: list[str]) -> NetworkLookup:
        return self.platform.registry.descriptor(account.provider).check_network_lookup(
            NetworkLookup(region=region, vpc=vpc, subnets=tuple(subnets))
        )

    def lookup(self, *, cloud_account_id: str, region: str, vpc: str, subnets: list[str]) -> NetworkDetails:
        """Fetch a network's details without registering it (the console's preview)."""
        self.principal.require(Permission.NETWORK_MANAGE)
        account = self._connected_account(cloud_account_id)
        lookup = self._checked_lookup(account, region, vpc, subnets)
        # A short, read-only lookup: the API waits for the monitoring-worker (docs/adr/0004).
        details = self.platform.tasks.run(
            "describe_network",
            {
                "account_id": str(account.id),
                "lookup": {"region": lookup.region, "vpc": lookup.vpc, "subnets": list(lookup.subnets)},
            },
            timeout=self.platform.settings.lookup_timeout_seconds,
            organization_id=account.organization_id,
            requested_by_id=self.principal.user_id,
        )
        return NetworkDetails(
            **{
                **details,
                "subnets": [SubnetInfo(**x) for x in details["subnets"]],
                "checks": [ValidationCheck(**c) for c in details["checks"]],
            }
        )

    # ----------------------------------------------------------------- writes

    def create(
        self,
        *,
        environment_id: str,
        name: str,
        cloud_account_id: str,
        region: str,
        vpc: str,
        subnets: list[str],
    ) -> Network:
        self.principal.require(Permission.NETWORK_MANAGE)
        org = self.principal.organization_id
        environment = queries.environment(self.session, org, environment_id)
        name = name.strip()
        if not NETWORK_NAME_RE.match(name):
            raise _field_error("name", "Must be 2-63 lowercase letters, digits or hyphens, starting with a letter.")
        if any(n.name == name for n in queries.networks(self.session, org, environment_id=environment.id)):
            raise Conflict(
                f"Environment '{environment.name}' already has a network named '{name}'.",
                details={"fields": {"name": "Name already in use."}},
            )
        account = self._connected_account(cloud_account_id)
        lookup = self._checked_lookup(account, region, vpc, subnets)
        for other in queries.networks(self.session, org, environment_id=environment.id):
            same = (other.cloud_account_id, other.region, other.vpc, sorted(other.subnets or []))
            if same == (account.id, lookup.region, lookup.vpc, sorted(lookup.subnets)):
                raise Conflict(f"This VPC and subnet are already registered in this environment as '{other.name}'.")

        network = Network(
            organization_id=org,
            environment_id=environment.id,
            cloud_account_id=account.id,
            name=name,
            provider=account.provider,
            region=lookup.region,
            vpc=lookup.vpc,
            subnets=list(lookup.subnets),
            status=NetworkStatus.PENDING.value,
            created_by_id=self.principal.user_id,
        )
        self.session.add(network)
        self.session.flush()
        record_audit(
            self.session,
            organization_id=org,
            principal=self.principal,
            event=AuditEvent.NETWORK_CREATED,
            resource_type="network",
            resource_id=network.id,
            resource_name=name,
            status=AuditStatus.SUCCESS,
            details={
                "environment": environment.name,
                "provider": account.provider,
                "cloud_account": account.name,
                "region": lookup.region,
                "vpc": lookup.vpc,
                "subnets": list(lookup.subnets),
            },
        )
        self.session.commit()
        self._validate(network, account)
        return network

    def validate(self, network_id: str) -> Network:
        self.principal.require(Permission.NETWORK_MANAGE)
        network = queries.network(self.session, self.principal.organization_id, network_id)
        account = self.session.get(CloudAccount, network.cloud_account_id)
        if account is None or account.status != CloudAccountStatus.CONNECTED:
            raise Conflict(
                "The network's cloud account is not connected.",
                code="CLOUD_ACCOUNT_NOT_CONNECTED",
                suggested_action="Validate the cloud account first, then the network.",
            )
        self._validate(network, account)
        return network

    def _validate(self, network: Network, account: CloudAccount) -> None:
        """The lookup runs in the monitoring-worker (read-only); the network shows VALIDATING until
        it records the result and the NETWORK_VALIDATED audit event."""
        network.status = NetworkStatus.VALIDATING.value
        self.session.commit()
        self.platform.tasks.submit(
            "validate_network",
            {"network_id": str(network.id)},
            organization_id=network.organization_id,
            requested_by_id=self.principal.user_id,
        )
        self.session.refresh(network)

    def delete(self, network_id: str) -> None:
        self.principal.require(Permission.NETWORK_MANAGE)
        network = queries.network(self.session, self.principal.organization_id, network_id)
        in_use = self.cluster_counts().get(network.id, 0)
        if in_use:
            raise Conflict(
                f"Network '{network.name}' is used by {plural(in_use, 'cluster')}.",
                code="NETWORK_IN_USE",
                suggested_action="Delete those clusters first.",
            )
        # Deleted clusters keep the network's details in their desired state.
        self.session.execute(update(Cluster).where(Cluster.network_id == network.id).values(network_id=None))
        record_audit(
            self.session,
            organization_id=network.organization_id,
            principal=self.principal,
            event=AuditEvent.NETWORK_DELETED,
            resource_type="network",
            resource_id=network.id,
            resource_name=network.name,
            status=AuditStatus.SUCCESS,
            details={"vpc": network.vpc, "subnets": list(network.subnets or [])},
        )
        self.session.delete(network)
        self.session.commit()
