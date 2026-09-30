"""Database engine abstraction.

Everything engine-specific (versions, topology, configuration, health semantics, scaling
rules) lives behind this interface so the platform core never branches on the engine.

Providers render what the infrastructure layer installs (topology, settings, version and
package pins). They never execute anything on the VMs; runtime actions on nodes go through
the agent's allowlist (docs/adr/0005, docs/adr/0007).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.domain.cluster_spec import ClusterSpec
from app.domain.errors import NotSupported
from app.domain.health import HealthAssessment, HealthThresholds, NodeObservation
from app.providers.cloud.base import MachineType, NodePlacement


@dataclass(frozen=True)
class EngineVersion:
    version: str
    label: str
    status: str
    default: bool = False
    distribution: str = ""
    license_review_status: str = ""
    notes: str = ""


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label: str
    unit: str  # percent | per_second | count | bytes | text


@dataclass(frozen=True)
class EngineCatalog:
    engine: str
    display_name: str
    description: str
    versions: tuple[EngineVersion, ...]
    default_version: str
    min_nodes: int
    max_nodes: int
    ha_min_nodes: int
    min_storage_gb: int
    max_storage_gb: int
    min_memory_gb: float
    default_machine_type: str
    ports: dict[str, int]
    metrics: tuple[MetricDefinition, ...]
    # Dedicated layout groups: name, label, count limits and defaults (docs/adr/0016).
    node_groups: tuple[dict[str, Any], ...] = ()
    # Settings users may change (docs/adr/0017).
    settings: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EngineProvisionPlan:
    nodes: list[NodePlacement]
    settings: dict[str, Any]


@dataclass
class AgentCommandSpec:
    """An approved action to run through one node agent."""

    command: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScalePlan:
    """Scale-up plan: the MVP only adds nodes (docs/adr/0010)."""

    current_count: int
    target_count: int
    add: list[NodePlacement]
    nodes: list[NodePlacement]  # full topology after scaling
    settings: dict[str, Any]


@dataclass
class ClusterMetrics:
    node_count: int
    nodes_reporting: int
    cpu_percent: float | None = None
    memory_percent: float | None = None
    disk_percent: float | None = None
    disk_used_bytes: int | None = None
    disk_total_bytes: int | None = None
    network_rx_bytes_per_sec: float | None = None
    network_tx_bytes_per_sec: float | None = None
    engine: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HealthContext:
    expected_nodes: list[str]
    high_availability: bool
    thresholds: HealthThresholds
    now: datetime
    # node name => group (dedicated layout, docs/adr/0016); empty for the combined layout.
    node_groups: dict[str, str] = field(default_factory=dict)


class DatabaseProvider(ABC):
    engine: str
    display_name: str
    aliases: tuple[str, ...] = ()

    @abstractmethod
    def catalog(self) -> EngineCatalog: ...

    @abstractmethod
    def resolve_version(self, version: str | None) -> str:
        """The exact version a new cluster gets: the catalog default when none is given,
        otherwise a supported catalog version. Raises ValidationFailed for anything else."""

    @abstractmethod
    def validate(
        self,
        spec: ClusterSpec,
        machine: MachineType | None = None,
        group_machines: dict[str, MachineType] | None = None,
    ) -> None:
        """Engine rules for a cluster spec; raise ValidationFailed with field details.

        ``machine`` is the machine type of a combined layout; ``group_machines`` those of a
        dedicated layout's groups."""

    @abstractmethod
    def provision(self, spec: ClusterSpec, zones: list[str]) -> EngineProvisionPlan:
        """Initial topology (node names, roles, zones) and engine settings for bootstrap."""

    @abstractmethod
    def configure(
        self,
        spec: ClusterSpec,
        nodes: list[NodePlacement],
        previous_settings: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Engine settings rendered onto the VMs (passed to the infrastructure layer)."""

    @abstractmethod
    def health(self, ctx: HealthContext, observations: list[NodeObservation]) -> HealthAssessment: ...

    @abstractmethod
    def metrics(self, observations: list[NodeObservation], stale_after: float) -> ClusterMetrics: ...

    @abstractmethod
    def scale(
        self,
        spec: ClusterSpec,
        current_nodes: list[NodePlacement],
        target_count: int,
        zones: list[str],
        previous_settings: dict[str, Any] | None,
        group: str | None = None,
    ) -> ScalePlan:
        """Plan adding nodes (to ``group`` in a dedicated layout). Raises ValidationFailed for fewer
        or equal nodes."""

    def load_balancer_targets(self, spec: ClusterSpec, nodes: list[NodePlacement]) -> list[str]:
        """Nodes behind the cluster's internal load balancer; none by default."""
        return []

    def settings_catalog(self) -> list[dict[str, Any]]:
        """Settings users may change (docs/adr/0017); none by default."""
        return []

    def merge_config(self, current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
        raise NotSupported(f"{self.display_name} configuration cannot be changed from the platform.")

    def config_plan(self, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        """What applying ``after`` over ``before`` involves: {"dynamic": [...], "static": [...]}."""
        return {"dynamic": [], "static": []}

    def config_file(self, spec: ClusterSpec, config: dict[str, Any]) -> dict[str, Any] | None:
        """The engine's configuration file as the platform renders it (docs/adr/0018), or None."""
        return None

    def engine_version_of(self, report_engine: dict[str, Any]) -> str | None:
        version = report_engine.get("version")
        return str(version) if version else None

    def upgrade(self, spec: ClusterSpec, target_version: str) -> None:
        raise NotSupported(
            f"{self.display_name} upgrades are not available in this release.",
            suggested_action="Rolling upgrades are planned for a later phase.",
        )

    def backup(self, spec: ClusterSpec) -> None:
        raise NotSupported(
            f"{self.display_name} backups are not available in this release.",
            suggested_action="Snapshot backups to Cloud Storage are planned for a later phase.",
        )

    def restore(self, spec: ClusterSpec, backup_id: str) -> None:
        raise NotSupported(f"{self.display_name} restore is not available in this release.")

    def delete(self, spec: ClusterSpec) -> list[str]:
        """Hook for engine-specific pre-delete steps; returns notes for the operation log."""
        return []
