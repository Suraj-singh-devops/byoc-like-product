"""Control-plane side of the node agent protocol.

1. Register: the agent presents a VM identity token signed by the cloud (GCE metadata
   server). The cloud provider verifies it; the instance must be a known node of the
   cluster. The agent receives a random per-node token (only its hash is stored).
2. Heartbeat: the agent posts its report and receives pending approved commands.
3. Command result: the agent reports the outcome of each command.

Commands come from a fixed allowlist; the agent enforces the same allowlist and never
executes arbitrary shell commands (docs/adr/0007). The MVP allowlist is restart_engine only;
drain_nodes/undrain_nodes return together with scale-down.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.health_service import HealthService
from app.application.platform import Platform
from app.domain.enums import AgentCommandStatus
from app.domain.errors import AuthenticationFailed, NotFound, ValidationFailed
from app.domain.states import ClusterLifecycle
from app.infrastructure.logging import get_logger
from app.infrastructure.metrics import AGENT_HEARTBEATS
from app.infrastructure.security import generate_agent_token, hash_token
from app.models import AgentCommand, CloudAccount, Cluster, ClusterNode
from app.providers.database.base import AgentCommandSpec

log = get_logger(__name__)

# Command -> allowed argument names.
ALLOWED_COMMANDS: dict[str, set[str]] = {
    "restart_engine": set(),
}
COMMAND_TTL = timedelta(minutes=15)
_INVALID_REGISTRATION = "Agent registration rejected."


def validate_command(spec: AgentCommandSpec) -> None:
    allowed_args = ALLOWED_COMMANDS.get(spec.command)
    if allowed_args is None:
        raise ValidationFailed(f"Command {spec.command!r} is not an approved agent command.")
    unknown = set(spec.args) - allowed_args
    if unknown:
        raise ValidationFailed(f"Unexpected arguments for {spec.command}: {sorted(unknown)}")


def queue_command(
    session: Session, node: ClusterNode, spec: AgentCommandSpec, operation_id: uuid.UUID | None
) -> AgentCommand:
    validate_command(spec)
    command = AgentCommand(
        cluster_id=node.cluster_id,
        node_id=node.id,
        operation_id=operation_id,
        command=spec.command,
        args=spec.args,
        status=AgentCommandStatus.PENDING.value,
        expires_at=datetime.now(UTC) + COMMAND_TTL,
    )
    session.add(command)
    session.flush()
    return command


class AgentService:
    def __init__(self, session: Session, platform: Platform) -> None:
        self.session = session
        self.platform = platform
        self.health = HealthService(platform)

    def register(
        self, cluster_id: uuid.UUID, node_name: str, identity_token: str, agent_version: str | None
    ) -> tuple[str, ClusterNode]:
        cluster = self.session.get(Cluster, cluster_id)
        if cluster is None or cluster.deleted_at is not None or cluster.lifecycle_state == ClusterLifecycle.DELETED:
            raise AuthenticationFailed(_INVALID_REGISTRATION)
        node = self.session.scalar(
            select(ClusterNode).where(
                ClusterNode.cluster_id == cluster.id,
                ClusterNode.name == node_name,
                ClusterNode.deleted_at.is_(None),
            )
        )
        account = self.session.get(CloudAccount, cluster.cloud_account_id) if cluster.cloud_account_id else None
        if node is None or account is None:
            raise AuthenticationFailed(_INVALID_REGISTRATION)
        # Verified with the cloud's public keys: no customer access needed (docs/adr/0004).
        identity = self.platform.registry.descriptor(cluster.cloud_provider).verify_instance_identity(
            account.project_id,
            identity_token,
            self.platform.settings.agent_identity_audience,
        )
        if identity.instance_name != node.instance_name or identity.zone != node.zone:
            log.warning(
                "agent_identity_mismatch",
                node=node.name,
                claimed_instance=identity.instance_name,
                expected_instance=node.instance_name,
            )
            raise AuthenticationFailed(_INVALID_REGISTRATION)
        token = generate_agent_token()
        node.agent_token_hash = hash_token(token)
        node.agent_registered_at = datetime.now(UTC)
        node.instance_id = identity.instance_id
        if agent_version:
            node.agent_version = agent_version
        self.session.commit()
        log.info("agent_registered", cluster_id=str(cluster.id), node=node.name)
        return token, node

    def authenticate(self, token: str) -> tuple[ClusterNode, Cluster]:
        node = self.session.scalar(select(ClusterNode).where(ClusterNode.agent_token_hash == hash_token(token)))
        if node is None or node.deleted_at is not None:
            AGENT_HEARTBEATS.labels("unauthorized").inc()
            raise AuthenticationFailed("Unknown agent token.", suggested_action="Re-register the agent.")
        cluster = self.session.get(Cluster, node.cluster_id)
        if cluster is None or cluster.deleted_at is not None:
            raise AuthenticationFailed("The cluster no longer exists.")
        return node, cluster

    def heartbeat(self, token: str, report: dict[str, Any]) -> list[AgentCommand]:
        node, cluster = self.authenticate(token)
        now = datetime.now(UTC)
        node.agent_last_seen_at = now
        self.health.ingest_report(cluster, node, report, source="heartbeat", received_at=now)
        commands = self.session.scalars(
            select(AgentCommand).where(
                AgentCommand.node_id == node.id,
                AgentCommand.status.in_([AgentCommandStatus.PENDING, AgentCommandStatus.SENT]),
            )
        ).all()
        deliver = []
        for command in commands:
            if command.expires_at <= now:
                command.status = AgentCommandStatus.EXPIRED.value
                command.message = "Expired before the agent executed it."
                continue
            command.status = AgentCommandStatus.SENT.value
            command.sent_at = command.sent_at or now
            deliver.append(command)
        self.session.commit()
        AGENT_HEARTBEATS.labels("ok").inc()
        return deliver

    def command_result(self, token: str, command_id: uuid.UUID, succeeded: bool, message: str, output: dict) -> None:
        node, _cluster = self.authenticate(token)
        command = self.session.get(AgentCommand, command_id)
        if command is None or command.node_id != node.id:
            raise NotFound("Command not found.")
        command.status = (AgentCommandStatus.SUCCEEDED if succeeded else AgentCommandStatus.FAILED).value
        command.completed_at = datetime.now(UTC)
        command.message = message[:2000]
        command.result = output
        self.session.commit()
