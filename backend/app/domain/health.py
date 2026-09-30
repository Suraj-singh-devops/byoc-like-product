"""Health model and the engine-agnostic node checks (docs/adr/0008-separate-lifecycle-and-health.md).

Health is evaluated for two components:

* infrastructure - the VM (from the cloud API) and its system metrics (from the agent);
* engine         - the database itself, evaluated by the engine's DatabaseProvider.

Warnings (resource pressure) are reported but never change a health state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.domain.reports import NodeReport
from app.domain.states import AgentStatus, ClusterHealth, NodeHealth, worst_cluster_health, worst_node_health

VM_DOWN_STATUSES = frozenset({"TERMINATED", "STOPPED", "STOPPING", "SUSPENDED", "SUSPENDING"})
VM_MISSING_STATUS = "NOT_FOUND"


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class HealthThresholds:
    agent_stale_seconds: float = 90
    disk_warning_percent: float = 80
    disk_critical_percent: float = 90
    cpu_warning_percent: float = 90
    memory_warning_percent: float = 95


@dataclass
class NodeObservation:
    """Everything the control plane currently knows about one node."""

    name: str
    ordinal: int
    zone: str | None = None
    role: str | None = None
    instance_status: str | None = None
    report: NodeReport | None = None
    report_age_seconds: float | None = None
    report_source: str | None = None
    bootstrap_status: str | None = None
    bootstrap_message: str | None = None

    @property
    def vm_down(self) -> bool:
        return self.instance_status in VM_DOWN_STATUSES

    @property
    def vm_missing(self) -> bool:
        return self.instance_status == VM_MISSING_STATUS

    def fresh_report(self, stale_after: float) -> NodeReport | None:
        if self.report is None or self.report_age_seconds is None:
            return None
        if self.report_age_seconds > stale_after:
            return None
        return self.report

    def agent_status(self, stale_after: float) -> AgentStatus:
        if self.report is None or self.report_age_seconds is None:
            return AgentStatus.NOT_REPORTED
        return AgentStatus.STALE if self.report_age_seconds > stale_after else AgentStatus.REPORTING


@dataclass
class NodeAssessment:
    name: str
    infrastructure: NodeHealth = NodeHealth.HEALTHY
    engine: NodeHealth = NodeHealth.UNKNOWN
    agent_status: AgentStatus = AgentStatus.NOT_REPORTED
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def state(self) -> NodeHealth:
        return worst_node_health((self.infrastructure, self.engine))

    def infrastructure_failed(self, reason: str) -> None:
        self.infrastructure = NodeHealth.UNHEALTHY
        self.reasons.append(reason)

    def engine_failed(self, reason: str) -> None:
        self.engine = NodeHealth.UNHEALTHY
        self.reasons.append(reason)

    def engine_unknown(self, reason: str) -> None:
        self.engine = NodeHealth.UNKNOWN
        self.reasons.append(reason)

    def warn(self, warning: str) -> None:
        self.warnings.append(warning)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "infrastructure": self.infrastructure.value,
            "engine": self.engine.value,
            "agent_status": self.agent_status.value,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
        }


@dataclass
class HealthAssessment:
    infrastructure: ClusterHealth
    engine: ClusterHealth
    reasons: list[str]
    warnings: list[str]
    nodes: list[NodeAssessment]
    nodes_expected: int
    nodes_reporting: int
    engine_status: str | None = None
    engine_node_count: int | None = None
    checked_at: datetime = field(default_factory=utcnow)

    @property
    def state(self) -> ClusterHealth:
        return worst_cluster_health((self.infrastructure, self.engine))

    def node(self, name: str) -> NodeAssessment | None:
        return next((n for n in self.nodes if n.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "infrastructure": self.infrastructure.value,
            "engine": self.engine.value,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "nodes": [n.to_dict() for n in self.nodes],
            "nodes_expected": self.nodes_expected,
            "nodes_reporting": self.nodes_reporting,
            "engine_status": self.engine_status,
            "engine_node_count": self.engine_node_count,
            "checked_at": self.checked_at.isoformat(),
        }


def evaluate_platform_node(obs: NodeObservation, thresholds: HealthThresholds) -> NodeAssessment:
    """VM, agent and system-resource checks shared by every engine.

    The engine component is left UNKNOWN (or UNHEALTHY when the VM is gone) for the engine's
    provider to evaluate from the agent's report.
    """
    node = NodeAssessment(name=obs.name, agent_status=obs.agent_status(thresholds.agent_stale_seconds))

    if obs.vm_missing:
        node.infrastructure_failed("VM missing: the instance no longer exists in the cloud project")
        node.engine = NodeHealth.UNHEALTHY
        return node
    if obs.vm_down:
        node.infrastructure_failed(f"VM unavailable: instance status is {obs.instance_status}")
        node.engine = NodeHealth.UNHEALTHY
        return node

    report = obs.fresh_report(thresholds.agent_stale_seconds)
    if report is None:
        # A running VM is not assumed dead because its agent is silent (TRD §34).
        if obs.instance_status is None:
            node.infrastructure = NodeHealth.UNKNOWN
        if obs.report is None:
            node.engine_unknown("Agent has not reported yet")
        else:
            node.engine_unknown(f"Agent unavailable: no report for {int(obs.report_age_seconds or 0)}s")
        return node

    system = report.system
    if system.disk_percent is not None:
        if system.disk_percent >= thresholds.disk_critical_percent:
            node.infrastructure_failed(
                f"Disk usage critical: {system.disk_percent:.0f}% (>= {thresholds.disk_critical_percent:.0f}%)"
            )
        elif system.disk_percent >= thresholds.disk_warning_percent:
            node.warn(f"Disk usage high: {system.disk_percent:.0f}% (>= {thresholds.disk_warning_percent:.0f}%)")
    if system.cpu_percent is not None and system.cpu_percent >= thresholds.cpu_warning_percent:
        node.warn(f"CPU usage high: {system.cpu_percent:.0f}%")
    if system.memory_percent is not None and system.memory_percent >= thresholds.memory_warning_percent:
        node.warn(f"Memory usage high: {system.memory_percent:.0f}%")
    return node


def rollup_infrastructure(nodes: list[NodeAssessment], expected: int) -> tuple[ClusterHealth, list[str]]:
    """Cluster infrastructure health from the nodes' VMs."""
    down = [n.name for n in nodes if n.infrastructure == NodeHealth.UNHEALTHY]
    if down:
        if expected and len(down) * 2 > expected:
            return ClusterHealth.UNHEALTHY, [f"{len(down)} of {expected} nodes are down or failing"]
        return ClusterHealth.DEGRADED, [f"Nodes down or failing: {', '.join(down)}"]
    if nodes and all(n.infrastructure == NodeHealth.UNKNOWN for n in nodes):
        return ClusterHealth.UNKNOWN, []
    return ClusterHealth.HEALTHY, []
