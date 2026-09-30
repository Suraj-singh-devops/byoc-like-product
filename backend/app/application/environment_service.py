"""Environments: organization-scoped groups of networks and clusters (docs/adr/0013)."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.application.audit_service import record_audit
from app.application.platform import Platform
from app.application.principal import Principal
from app.domain.enums import AuditEvent, AuditStatus, EnvironmentType
from app.domain.errors import Conflict, ValidationFailed
from app.domain.rbac import Permission
from app.domain.text import plural
from app.models import Cluster, Environment
from app.repositories import queries

# 2-40 lowercase letters, digits and hyphens, starting with a letter: "production", "test-eu".
ENVIRONMENT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$")


@dataclass
class EnvironmentView:
    environment: Environment
    network_count: int = 0
    cluster_count: int = 0
    providers: list[str] = field(default_factory=list)


class EnvironmentService:
    def __init__(self, session: Session, platform: Platform, principal: Principal) -> None:
        self.session = session
        self.platform = platform
        self.principal = principal

    def _views(self, environments: list[Environment]) -> list[EnvironmentView]:
        org = self.principal.organization_id
        networks = defaultdict(list)
        for network in queries.networks(self.session, org):
            networks[network.environment_id].append(network)
        clusters = queries.cluster_counts_by_environment(self.session, org)
        return [
            EnvironmentView(
                environment=env,
                network_count=len(networks[env.id]),
                cluster_count=clusters.get(env.id, 0),
                providers=sorted({n.provider for n in networks[env.id]}),
            )
            for env in environments
        ]

    def list(self) -> list[EnvironmentView]:
        self.principal.require(Permission.ENVIRONMENT_READ)
        return self._views(list(queries.environments(self.session, self.principal.organization_id)))

    def get(self, environment_id: str) -> EnvironmentView:
        self.principal.require(Permission.ENVIRONMENT_READ)
        environment = queries.environment(self.session, self.principal.organization_id, environment_id)
        return self._views([environment])[0]

    def create(self, *, name: str, type_: str, description: str | None) -> Environment:
        self.principal.require(Permission.ENVIRONMENT_MANAGE)
        org = self.principal.organization_id
        name = name.strip()
        if not ENVIRONMENT_NAME_RE.match(name):
            message = "Must be 2-40 lowercase letters, digits or hyphens, starting with a letter (e.g. production)."
            raise ValidationFailed(message, details={"fields": {"name": message}})
        try:
            kind = EnvironmentType(type_.strip().upper())
        except ValueError as exc:
            message = "Choose TEST or PRODUCTION."
            raise ValidationFailed(message, details={"fields": {"type": message}}) from exc
        if queries.environment_name_taken(self.session, org, name):
            raise Conflict(
                f"An environment named '{name}' already exists.", details={"fields": {"name": "Name already in use."}}
            )
        environment = Environment(
            organization_id=org,
            name=name,
            type=kind.value,
            description=(description or "").strip() or None,
            created_by_id=self.principal.user_id,
        )
        self.session.add(environment)
        self.session.flush()
        record_audit(
            self.session,
            organization_id=org,
            principal=self.principal,
            event=AuditEvent.ENVIRONMENT_CREATED,
            resource_type="environment",
            resource_id=environment.id,
            resource_name=name,
            status=AuditStatus.SUCCESS,
            details={"type": kind.value},
        )
        self.session.commit()
        return environment

    def delete(self, environment_id: str) -> None:
        """Only an empty environment can be removed: no networks, no clusters that are not deleted."""
        self.principal.require(Permission.ENVIRONMENT_MANAGE)
        view = self.get(environment_id)
        environment = view.environment
        if view.network_count or view.cluster_count:
            raise Conflict(
                f"Environment '{environment.name}' still has {plural(view.cluster_count, 'cluster')} and "
                f"{plural(view.network_count, 'network')}.",
                code="ENVIRONMENT_NOT_EMPTY",
                suggested_action="Delete its clusters first, then remove its networks.",
            )
        # Deleted clusters keep the environment's name in their desired state.
        self.session.execute(
            update(Cluster).where(Cluster.environment_id == environment.id).values(environment_id=None)
        )
        record_audit(
            self.session,
            organization_id=environment.organization_id,
            principal=self.principal,
            event=AuditEvent.ENVIRONMENT_DELETED,
            resource_type="environment",
            resource_id=environment.id,
            resource_name=environment.name,
            status=AuditStatus.SUCCESS,
            details={"type": environment.type},
        )
        self.session.delete(environment)
        self.session.commit()
