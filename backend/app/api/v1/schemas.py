"""Request/response models of the v1 API.

Responses are built explicitly from ORM objects (never ``from_attributes``) so that secret
columns such as ``agent_token_hash`` cannot leak by accident.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.states import TERMINAL_OPERATION_STATUSES, OperationStatus
from app.models import AuditLog, CloudAccount, Cluster, ClusterEvent, ClusterNode, Environment, Network, Operation


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class Message(BaseModel):
    message: str


# ------------------------------------------------------------------------- auth


class LoginRequest(Strict):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)
    organization_id: uuid.UUID | None = None


class SignupRequest(Strict):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)
    organization_name: str = Field(min_length=1, max_length=200)


class SwitchOrganizationRequest(Strict):
    organization_id: uuid.UUID


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


class MembershipOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    role: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_at: datetime
    user: UserOut
    organization: OrganizationOut
    role: str


class MeResponse(BaseModel):
    user: UserOut
    organization: OrganizationOut
    role: str
    permissions: list[str]
    organizations: list[MembershipOut]


class MetaResponse(BaseModel):
    name: str
    version: str
    mock_mode: bool
    signup_enabled: bool
    demo_accounts: list[dict[str, str]]


# ---------------------------------------------------------------------- members


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    role: str
    joined_at: datetime


class MemberCreateRequest(Strict):
    email: str = Field(max_length=320)
    name: str | None = Field(default=None, max_length=200)
    role: str
    password: str | None = Field(default=None, max_length=200)


class MemberUpdateRequest(Strict):
    role: str


# ---------------------------------------------------------------- cloud accounts


class CloudAccountCreateRequest(Strict):
    name: str = Field(min_length=2, max_length=100)
    provider: str = Field(default="gcp", max_length=20, description="gcp or aws.")
    project_id: str = Field(max_length=100, description="GCP project ID, or the 12-digit AWS account ID.")
    region: str = Field(min_length=3, max_length=50)
    service_account_email: str | None = Field(
        default=None,
        max_length=320,
        description="GCP only: service account in the customer's project that the platform impersonates.",
    )
    role_arn: str | None = Field(
        default=None,
        max_length=2048,
        description="AWS only: role in the customer's account that the platform assumes with the "
        "organization's external ID.",
    )
    validate_now: bool = True

    @model_validator(mode="before")
    @classmethod
    def _no_keys(cls, data: Any) -> Any:
        if isinstance(data, dict) and ("service_account_key" in data or data.get("auth_type") == "service_account_key"):
            raise ValueError(
                "Service-account keys are not accepted. Grant the platform permission to impersonate a "
                "service account in your project and send its email as service_account_email."
            )
        return data


class CloudAccountOut(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    project_id: str = Field(description="GCP project ID, or the AWS account ID.")
    region: str | None
    auth_type: str
    service_account_email: str | None
    role_arn: str | None
    status: str
    validation: dict[str, Any] | None
    last_validated_at: datetime | None
    last_connected_at: datetime | None
    cluster_count: int
    network_count: int
    created_at: datetime

    @classmethod
    def build(cls, account: CloudAccount, cluster_count: int = 0, network_count: int = 0) -> CloudAccountOut:
        return cls(
            id=account.id,
            name=account.name,
            provider=account.provider,
            project_id=account.project_id,
            region=account.region,
            auth_type=account.auth_type,
            service_account_email=account.service_account_email,
            role_arn=account.role_arn,
            status=account.status,
            validation=account.validation_result,
            last_validated_at=account.last_validated_at,
            last_connected_at=account.last_connected_at,
            cluster_count=cluster_count,
            network_count=network_count,
            created_at=account.created_at,
        )


class CloudOnboardingOut(BaseModel):
    """What a customer grants the platform (docs/adr/0003, docs/adr/0014)."""

    provider: str
    auth_type: str
    principal: str
    role_name: str
    permissions: list[str]
    external_id: str | None = None
    trust_policy: dict[str, Any] | None = None


class CloudProviderOut(BaseModel):
    name: str
    display_name: str
    auth_type: str
    simulated: bool
    default_machine_type: str


class RegionOut(BaseModel):
    name: str
    zones: list[str]
    location: str


class MachineTypeOut(BaseModel):
    name: str
    vcpus: int
    memory_gb: float
    architecture: str


class StorageTypeOut(BaseModel):
    name: str
    description: str


class CloudCatalogOut(BaseModel):
    provider: str
    simulated: bool
    default_machine_type: str
    regions: list[RegionOut]
    storage_types: list[StorageTypeOut]


# ------------------------------------------------------------- environments


class EnvironmentCreateRequest(Strict):
    name: str = Field(min_length=2, max_length=40, description="e.g. production, test-eu")
    type: str = Field(description="TEST or PRODUCTION.")
    description: str | None = Field(default=None, max_length=200)


class EnvironmentOut(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    description: str | None
    network_count: int
    cluster_count: int
    providers: list[str]
    created_at: datetime

    @classmethod
    def build(
        cls, environment: Environment, network_count: int, cluster_count: int, providers: list[str]
    ) -> EnvironmentOut:
        return cls(
            id=environment.id,
            name=environment.name,
            type=environment.type,
            description=environment.description,
            network_count=network_count,
            cluster_count=cluster_count,
            providers=providers,
            created_at=environment.created_at,
        )


# ----------------------------------------------------------------- networks


class NetworkLookupRequest(Strict):
    cloud_account_id: uuid.UUID
    region: str = Field(min_length=3, max_length=50)
    vpc: str = Field(min_length=1, max_length=255, description="GCP: VPC network name. AWS: VPC ID.")
    subnets: list[str] = Field(
        min_length=1, max_length=6, description="GCP: one subnet name. AWS: subnet IDs, at most one per zone."
    )


class NetworkCreateRequest(NetworkLookupRequest):
    name: str = Field(min_length=2, max_length=63)


class NetworkDetailsOut(BaseModel):
    valid: bool
    region: str
    vpc: str
    vpc_name: str
    vpc_cidrs: list[str]
    subnets: list[dict[str, Any]]
    zones: list[str]
    checks: list[dict[str, Any]]
    warnings: list[str]
    error: dict[str, Any] | None


class NetworkOut(BaseModel):
    id: uuid.UUID
    environment_id: uuid.UUID
    name: str
    provider: str
    cloud_account_id: uuid.UUID
    cloud_account_name: str | None
    region: str
    vpc: str
    subnets: list[str]
    status: str
    zones: list[str]
    details: dict[str, Any] | None
    cluster_count: int
    last_validated_at: datetime | None
    last_available_at: datetime | None
    created_at: datetime

    @classmethod
    def build(cls, network: Network, account_name: str | None, cluster_count: int) -> NetworkOut:
        details = network.details or {}
        return cls(
            id=network.id,
            environment_id=network.environment_id,
            name=network.name,
            provider=network.provider,
            cloud_account_id=network.cloud_account_id,
            cloud_account_name=account_name,
            region=network.region,
            vpc=network.vpc,
            subnets=list(network.subnets or []),
            status=network.status,
            zones=list(details.get("zones") or []),
            details=network.details,
            cluster_count=cluster_count,
            last_validated_at=network.last_validated_at,
            last_available_at=network.last_available_at,
            created_at=network.created_at,
        )


# --------------------------------------------------------------------- clusters


class NodeGroupRequest(Strict):
    count: int = Field(ge=0, le=50)
    machine_type: str = Field(max_length=63)
    storage_gb: int = Field(default=20, ge=10, le=65536)


class ClusterCreateRequest(Strict):
    name: str = Field(min_length=3, max_length=40)
    engine: str = "elasticsearch"
    version: str | None = Field(default=None, description="Exact catalog version; omitted means the default.")
    environment_id: uuid.UUID
    network_id: uuid.UUID = Field(description="A registered network of the environment; sets account and region.")
    zone: str | None = Field(
        default=None, max_length=50, description="Preferred zone of the network; with HA, the first of three."
    )
    cloud_account_id: uuid.UUID | None = Field(default=None, description="Optional; must be the network's.")
    region: str | None = Field(default=None, max_length=50, description="Optional; must be the network's.")
    layout: Literal["combined", "dedicated"] = Field(
        default="combined",
        description="combined: every node has every role. dedicated: master, data and coordinating groups "
        "(docs/adr/0016).",
    )
    # combined layout
    machine_type: str | None = Field(default=None, max_length=63)
    node_count: int | None = Field(default=None, ge=1, le=50)
    storage_gb: int | None = Field(default=None, ge=10, le=65536)
    # dedicated layout
    node_groups: dict[str, NodeGroupRequest] | None = None
    storage_type: str = "pd-balanced"
    high_availability: bool = False
    config: dict[str, Any] | None = Field(default=None, description="Initial setting overrides (docs/adr/0017).")

    @model_validator(mode="after")
    def _layout_fields(self) -> ClusterCreateRequest:
        if self.layout == "combined":
            missing = [f for f in ("machine_type", "node_count", "storage_gb") if getattr(self, f) is None]
            if missing:
                raise ValueError(f"The combined layout needs {', '.join(missing)}.")
        elif not self.node_groups:
            raise ValueError("The dedicated layout needs node_groups (master, data, coordinating).")
        return self


class ClusterScaleRequest(Strict):
    node_count: int = Field(ge=1, le=50, description="Must be larger than the current node count (of the group).")
    group: str | None = Field(default=None, description="Dedicated layout: data or coordinating.")


class ConfigUpdateRequest(Strict):
    settings: dict[str, Any] = Field(
        min_length=1, description="Setting => new value, or null to go back to the default (docs/adr/0017)."
    )


class OperationAccepted(BaseModel):
    cluster_id: uuid.UUID
    operation_id: uuid.UUID
    lifecycle: str


class OperationBrief(BaseModel):
    id: uuid.UUID
    operation_type: str
    status: str
    progress: int
    current_step: str | None
    cancel_requested: bool

    @classmethod
    def build(cls, op: Operation | None) -> OperationBrief | None:
        if op is None:
            return None
        return cls(
            id=op.id,
            operation_type=op.operation_type,
            status=op.status,
            progress=op.progress,
            current_step=op.current_step,
            cancel_requested=op.cancel_requested,
        )


class ClusterSummary(BaseModel):
    id: uuid.UUID
    name: str
    engine: str
    engine_version: str
    cloud_provider: str
    project_id: str
    environment: dict[str, Any] | None = Field(
        description="{id, name, type}; null for clusters created before environments."
    )
    network: dict[str, Any] | None = Field(description="{id, name, vpc, subnets}; null for a dedicated VPC.")
    region: str
    zone: str
    zones: list[str]
    layout: str
    node_groups: list[dict[str, Any]]
    endpoint: str | None = Field(description="Internal load balancer address (dedicated layout).")
    machine_type: str
    node_count: int
    storage_gb: int
    storage_type: str
    high_availability: bool
    lifecycle: str
    health: str
    status_message: str | None
    metrics: dict[str, Any]
    active_operation: OperationBrief | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def fields_from(cls, cluster: Cluster, active: Operation | None) -> dict[str, Any]:
        # Environment and network as recorded at creation: they never change for a cluster.
        desired = cluster.desired_state or {}
        return {
            "id": cluster.id,
            "name": cluster.name,
            "engine": cluster.engine,
            "engine_version": cluster.engine_version,
            "cloud_provider": cluster.cloud_provider,
            "project_id": cluster.project_id,
            "environment": desired.get("environment"),
            "network": desired.get("network"),
            "region": cluster.region,
            "zone": cluster.zone,
            "zones": list(desired.get("cloud", {}).get("zones") or [cluster.zone]),
            "layout": desired.get("layout") or "combined",
            "node_groups": [
                {
                    "name": g.get("name"),
                    "count": g.get("count"),
                    "machine_type": g.get("machineType"),
                    "storage_gb": g.get("storageGB"),
                }
                for g in desired.get("nodeGroups") or []
            ],
            "endpoint": ((cluster.actual_state or {}).get("infrastructure") or {}).get("endpoint"),
            "machine_type": cluster.machine_type,
            "node_count": cluster.node_count,
            "storage_gb": cluster.storage_gb,
            "storage_type": cluster.storage_type,
            "high_availability": cluster.high_availability,
            "lifecycle": cluster.lifecycle_state,
            "health": cluster.health,
            "status_message": cluster.status_message,
            "metrics": cluster.metrics_summary or {},
            "active_operation": OperationBrief.build(active),
            "created_at": cluster.created_at,
            "updated_at": cluster.updated_at,
        }

    @classmethod
    def build(cls, cluster: Cluster, active: Operation | None) -> ClusterSummary:
        return cls(**cls.fields_from(cluster, active))


class NodeOut(BaseModel):
    id: uuid.UUID
    name: str
    ordinal: int
    role: str
    node_group: str | None
    machine_type: str | None
    zone: str
    instance_name: str | None
    instance_id: str | None
    hostname: str | None
    private_ip: str | None
    lifecycle: str
    instance_status: str | None
    agent_status: str
    health: str
    infrastructure_health: str | None
    engine_health: str | None
    health_reasons: list[str]
    health_warnings: list[str]
    engine_version: str | None
    agent_version: str | None
    agent_registered: bool
    last_report_at: datetime | None
    report_source: str | None
    bootstrap_status: str | None
    system: dict[str, Any]
    engine_metrics: dict[str, Any]

    @classmethod
    def build(cls, node: ClusterNode, health_details: dict[str, Any] | None = None) -> NodeOut:
        report = node.last_report or {}
        engine = report.get("engine") or {}
        components = next((n for n in (health_details or {}).get("nodes", []) if n.get("name") == node.name), {})
        return cls(
            id=node.id,
            name=node.name,
            ordinal=node.ordinal,
            role=node.role,
            node_group=node.node_group,
            machine_type=node.machine_type,
            zone=node.zone,
            instance_name=node.instance_name,
            instance_id=node.instance_id,
            hostname=node.hostname,
            private_ip=node.private_ip,
            lifecycle=node.lifecycle_state,
            instance_status=node.instance_status,
            agent_status=node.agent_status,
            health=node.health,
            infrastructure_health=components.get("infrastructure"),
            engine_health=components.get("engine"),
            health_reasons=list(node.health_reasons or []),
            health_warnings=list(node.health_warnings or []),
            engine_version=node.engine_version,
            agent_version=node.agent_version,
            agent_registered=node.agent_token_hash is not None,
            last_report_at=node.last_report_at,
            report_source=node.report_source,
            bootstrap_status=node.bootstrap_status,
            system=report.get("system") or {},
            engine_metrics={
                k: engine.get(k)
                for k in ("jvm_heap_percent", "search_rate", "indexing_rate", "shards", "is_master", "reachable")
            },
        )


class ClusterDetail(ClusterSummary):
    cloud_account_id: uuid.UUID | None
    cloud_account_name: str | None
    simulated: bool
    desired_state: dict[str, Any]
    actual_state: dict[str, Any]
    generation: int
    observed_generation: int
    health_details: dict[str, Any]
    resource_prefix: str
    created_by: str | None
    nodes: list[NodeOut]
    last_health_check_at: datetime | None
    deleted_at: datetime | None


class EventOut(BaseModel):
    id: uuid.UUID
    cluster_id: uuid.UUID
    cluster_name: str | None = None
    node_name: str | None
    event_type: str
    severity: str
    message: str
    created_at: datetime

    @classmethod
    def build(cls, event: ClusterEvent, cluster_name: str | None = None) -> EventOut:
        return cls(
            id=event.id,
            cluster_id=event.cluster_id,
            cluster_name=cluster_name,
            node_name=event.node_name,
            event_type=event.event_type,
            severity=event.severity,
            message=event.message,
            created_at=event.created_at,
        )


# ------------------------------------------------------------------- operations


class OperationOut(BaseModel):
    id: uuid.UUID
    cluster_id: uuid.UUID | None
    cluster_name: str | None
    operation_type: str
    status: str
    is_terminal: bool
    current_step: str | None
    progress: int
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    error_message: str | None
    error: dict[str, Any] | None
    params: dict[str, Any]
    steps: list[dict[str, Any]]
    log: list[dict[str, Any]]
    result: dict[str, Any] | None
    cancel_requested: bool
    cancellable: bool
    retry_of: str | None
    attempt: int
    created_by: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def build(
        cls, op: Operation, cluster_name: str | None, created_by: str | None, *, with_log: bool = True
    ) -> OperationOut:
        metadata = op.metadata_ or {}
        terminal = OperationStatus(op.status) in TERMINAL_OPERATION_STATUSES
        return cls(
            id=op.id,
            cluster_id=op.cluster_id,
            cluster_name=cluster_name,
            operation_type=op.operation_type,
            status=op.status,
            is_terminal=terminal,
            current_step=op.current_step,
            progress=op.progress,
            started_at=op.started_at,
            completed_at=op.completed_at,
            error_code=op.error_code,
            error_message=op.error_message,
            error=metadata.get("error"),
            params=metadata.get("params", {}),
            steps=metadata.get("steps", []),
            log=metadata.get("log", []) if with_log else [],
            result=metadata.get("result"),
            cancel_requested=op.cancel_requested,
            cancellable=not terminal and not op.cancel_requested and op.operation_type != "DELETE_CLUSTER",
            retry_of=metadata.get("retry_of"),
            attempt=op.attempt,
            created_by=created_by,
            created_at=op.created_at,
            updated_at=op.updated_at,
        )


# ------------------------------------------------------------------------ audit


class AuditLogOut(BaseModel):
    id: uuid.UUID
    user: str | None
    user_id: uuid.UUID | None
    organization: str
    action: str
    resource: str | None
    resource_type: str
    resource_id: str | None
    timestamp: datetime
    status: str
    details: dict[str, Any]
    operation_id: uuid.UUID | None
    ip_address: str | None

    @classmethod
    def build(cls, entry: AuditLog, organization_name: str) -> AuditLogOut:
        return cls(
            id=entry.id,
            user=entry.user_email,
            user_id=entry.user_id,
            organization=organization_name,
            action=entry.action,
            resource=entry.resource_name,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            timestamp=entry.created_at,
            status=entry.status,
            details=entry.details or {},
            operation_id=entry.operation_id,
            ip_address=entry.ip_address,
        )


# ------------------------------------------------------------------------ agent


class AgentRegisterRequest(Strict):
    cluster_id: uuid.UUID
    node_name: str = Field(min_length=1, max_length=63)
    identity_token: str = Field(min_length=10, max_length=8192)
    agent_version: str | None = Field(default=None, max_length=40)


class AgentRegisterResponse(BaseModel):
    agent_token: str
    node_id: uuid.UUID
    heartbeat_interval_seconds: int


class AgentHeartbeatRequest(BaseModel):
    report: dict[str, Any]


class AgentCommandOut(BaseModel):
    id: uuid.UUID
    command: str
    args: dict[str, Any]


class AgentHeartbeatResponse(BaseModel):
    commands: list[AgentCommandOut]
    heartbeat_interval_seconds: int


class AgentCommandResultRequest(Strict):
    status: Literal["succeeded", "failed"]
    message: str = Field(default="", max_length=2000)
    output: dict[str, Any] = Field(default_factory=dict)


# ------------------------------------------------------------------------- mock


class FaultRequest(Strict):
    node_name: str
    fault: Literal["vm_down", "agent_down", "es_down", "disk_pressure", "heap_pressure", "cpu_spike", "clear"]


class IdentityTokenResponse(BaseModel):
    identity_token: str
    audience: str
