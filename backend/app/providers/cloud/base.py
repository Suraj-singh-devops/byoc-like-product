"""Cloud provider abstraction.

Terraform is the only writer of customer infrastructure (docs/adr/0005). This interface
therefore covers validation, discovery, read-only status, instance-identity verification and
Terraform orchestration; it deliberately has no imperative create or delete calls:

* validate_credentials()                        access and permissions in the account
* list_machine_types() / validate_placement()   what the account offers
* describe_network()                            a customer's existing VPC and subnets (read-only)
* plan_infrastructure() + apply_infrastructure() firewall, IAM, storage and compute as one plan
* destroy_infrastructure()                      everything the cluster's Terraform created
* get_resource_status() / read_node_reports()   read-only status

Static knowledge (catalog, identifier checks, onboarding, identity verification) lives in each
provider's CloudDescriptor (descriptor.py), which every process may use.

Implementations: GCPProvider (real), and the simulated MockGcpProvider and MockAwsProvider used
in MOCK_MODE (docs/adr/0014). Azure will implement the same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from app.domain.network import NetworkRef

if TYPE_CHECKING:
    from app.providers.cloud.descriptor import CloudDescriptor


@dataclass(frozen=True)
class CloudAccountContext:
    """What a provider needs to act in a customer account. Holds no credential material: access
    is by impersonating ``service_account_email`` on GCP (docs/adr/0003) or by assuming
    ``role_arn`` with the organization's ``external_id`` on AWS (docs/adr/0014)."""

    account_id: str
    organization_id: str
    provider: str
    # GCP project ID, or the 12-digit AWS account ID.
    project_id: str
    auth_type: str
    service_account_email: str | None = None
    region: str | None = None
    role_arn: str | None = None
    external_id: str | None = None


@dataclass(frozen=True)
class AccountRegistration:
    """Identifiers of a cloud account as entered by the user, before any cloud call."""

    project_id: str
    service_account_email: str | None = None
    role_arn: str | None = None


@dataclass
class ValidationCheck:
    key: str
    name: str
    status: str  # passed | failed | warning | skipped
    message: str = ""


@dataclass
class CredentialValidationResult:
    valid: bool
    checks: list[ValidationCheck]
    missing_permissions: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "checks": [asdict(c) for c in self.checks],
            "missing_permissions": list(self.missing_permissions),
            "error": self.error,
        }


@dataclass(frozen=True)
class Region:
    name: str
    zones: tuple[str, ...]
    location: str = ""


@dataclass(frozen=True)
class MachineType:
    name: str
    vcpus: int
    memory_gb: float
    architecture: str = "x86_64"
    description: str = ""


@dataclass(frozen=True)
class StorageType:
    name: str
    description: str


@dataclass(frozen=True)
class NetworkLookup:
    """Identifiers of an existing network as entered by the user (docs/adr/0013)."""

    region: str
    # GCP: VPC network name; AWS: VPC ID
    vpc: str
    # GCP: one subnet name; AWS: subnet IDs
    subnets: tuple[str, ...]


@dataclass
class SubnetInfo:
    id: str  # GCP: projects/<p>/regions/<r>/subnetworks/<name>; AWS: subnet ID
    name: str
    cidr: str
    zone: str | None  # AWS availability zone; None for a regional GCP subnet
    available_ips: int


@dataclass
class NetworkDetails:
    """What the cloud says about a network. Everything except the identifiers comes from here."""

    valid: bool
    region: str
    vpc: str  # GCP: projects/<p>/global/networks/<name>; AWS: VPC ID
    vpc_name: str
    vpc_cidrs: list[str]
    subnets: list[SubnetInfo]
    zones: list[str]
    checks: list[ValidationCheck]
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "region": self.region,
            "vpc": self.vpc,
            "vpc_name": self.vpc_name,
            "vpc_cidrs": list(self.vpc_cidrs),
            "subnets": [asdict(s) for s in self.subnets],
            "zones": list(self.zones),
            "checks": [asdict(c) for c in self.checks],
            "warnings": list(self.warnings),
            "error": self.error,
        }


@dataclass(frozen=True)
class NodePlacement:
    """Where and as what an engine node runs."""

    name: str
    ordinal: int
    zone: str
    roles: tuple[str, ...]
    # Dedicated layout (docs/adr/0016): the node's group and its own machine type and data disk;
    # None means the request's machine_type and storage_gb.
    group: str | None = None
    machine_type: str | None = None
    storage_gb: int | None = None


@dataclass
class InfrastructureRequest:
    cluster_id: str
    organization_id: str
    resource_prefix: str
    engine: str
    engine_version: str
    project_id: str
    region: str
    zone: str
    machine_type: str
    storage_gb: int
    storage_type: str
    high_availability: bool
    nodes: list[NodePlacement]
    engine_settings: dict[str, Any]
    labels: dict[str, str]
    # The registered network to run in; None for clusters created before networks existed,
    # which have a dedicated VPC (docs/adr/0013).
    network: NetworkRef | None = None
    # An internal load balancer in front of these nodes (dedicated layout: the coordinating nodes).
    load_balancer_nodes: list[str] = field(default_factory=list)

    def instance_name(self, node_name: str) -> str:
        return f"{self.resource_prefix}-{node_name}"


@dataclass
class ResourceChange:
    address: str
    actions: list[str]


@dataclass
class PlanSummary:
    to_add: int = 0
    to_change: int = 0
    to_destroy: int = 0
    changes: list[ResourceChange] = field(default_factory=list)

    def describe(self) -> str:
        return f"Plan: {self.to_add} to add, {self.to_change} to change, {self.to_destroy} to destroy."


@dataclass
class ProvisionedNode:
    name: str
    instance_name: str
    instance_id: str
    zone: str
    private_ip: str
    hostname: str


@dataclass
class InfrastructureState:
    nodes: list[ProvisionedNode]
    resources: list[str] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NodeRef:
    name: str
    instance_name: str
    zone: str


@dataclass
class GuestReport:
    """What a VM published about itself (bootstrap progress and the agent's last report)."""

    node_name: str
    bootstrap: dict[str, Any] | None = None
    report: dict[str, Any] | None = None


@dataclass(frozen=True)
class InstanceIdentity:
    project_id: str
    zone: str
    instance_name: str
    instance_id: str


class ProgressReporter(Protocol):
    def message(self, text: str) -> None: ...

    def heartbeat(self) -> None: ...

    def check_cancelled(self) -> None: ...

    def sleep(self, seconds: float) -> None: ...


class CloudProvider(ABC):
    """Reaches into customer accounts. Only the terraform-runner (writes, through Terraform) and the
    monitoring-worker (reads) build providers (docs/adr/0004); every other process uses the
    provider's static CloudDescriptor."""

    descriptor: CloudDescriptor

    @property
    def name(self) -> str:
        return self.descriptor.name

    @property
    def display_name(self) -> str:
        return self.descriptor.display_name

    @property
    def simulated(self) -> bool:
        return self.descriptor.simulated

    @abstractmethod
    def validate_credentials(self, account: CloudAccountContext) -> CredentialValidationResult: ...

    @abstractmethod
    def list_machine_types(self, account: CloudAccountContext, zone: str) -> list[MachineType]: ...

    @abstractmethod
    def validate_placement(
        self, account: CloudAccountContext, region: str, zone: str, machine_type: str
    ) -> MachineType:
        """Check in the account that the region, zone and machine type exist; return the machine type."""

    @abstractmethod
    def describe_network(self, account: CloudAccountContext, lookup: NetworkLookup) -> NetworkDetails:
        """Read-only lookup of an existing VPC and subnets (docs/adr/0013). Problems the customer
        must fix are reported as failed checks, not raised."""

    @abstractmethod
    def plan_infrastructure(
        self, account: CloudAccountContext, request: InfrastructureRequest, progress: ProgressReporter
    ) -> PlanSummary:
        """Plan the change. Must refuse any plan that destroys or replaces a stateful resource
        (VM or data disk); only destroy_infrastructure removes them."""

    @abstractmethod
    def apply_infrastructure(
        self, account: CloudAccountContext, request: InfrastructureRequest, progress: ProgressReporter
    ) -> InfrastructureState: ...

    @abstractmethod
    def destroy_infrastructure(
        self, account: CloudAccountContext, request: InfrastructureRequest, progress: ProgressReporter
    ) -> None: ...

    @abstractmethod
    def get_resource_status(self, account: CloudAccountContext, nodes: list[NodeRef]) -> dict[str, str]:
        """Instance status per node name: RUNNING, TERMINATED, ..., or NOT_FOUND."""

    @abstractmethod
    def read_node_reports(self, account: CloudAccountContext, nodes: list[NodeRef]) -> dict[str, GuestReport]: ...
