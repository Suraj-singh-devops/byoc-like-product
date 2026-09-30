"""Collects node observations, evaluates health and detects failures.

Reports reach the control plane two ways, both funnelled through ``ingest_report``:

* pushed by the agent over HTTPS (heartbeat), when the control plane is reachable, or
* pulled from the cloud (GCE guest attributes / the simulated data plane) by the monitor.

Health evaluation writes health only; lifecycle belongs to operations (docs/adr/0008).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.application.platform import Platform
from app.application.provisioning.outcome import add_event
from app.domain.enums import EventSeverity
from app.domain.health import HealthAssessment, HealthThresholds, NodeObservation
from app.domain.reports import NodeReport
from app.domain.states import AgentStatus, ClusterHealth, NodeHealth
from app.infrastructure.logging import get_logger
from app.models import Cluster, ClusterNode, MetricSample
from app.providers.cloud.base import CloudAccountContext, NodeRef
from app.providers.database.base import HealthContext

log = get_logger(__name__)

_CLUSTER_SEVERITY = {
    ClusterHealth.UNHEALTHY: EventSeverity.CRITICAL,
    ClusterHealth.DEGRADED: EventSeverity.WARNING,
    ClusterHealth.UNKNOWN: EventSeverity.WARNING,
    ClusterHealth.HEALTHY: EventSeverity.INFO,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def node_refs(nodes: list[ClusterNode]) -> list[NodeRef]:
    return [
        NodeRef(name=n.name, instance_name=n.instance_name or n.name, zone=n.zone) for n in nodes if n.instance_name
    ]


class HealthService:
    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.settings = platform.settings

    @property
    def thresholds(self) -> HealthThresholds:
        return HealthThresholds(agent_stale_seconds=self.settings.agent_stale_seconds)

    # ----------------------------------------------------------------- ingest

    def ingest_report(
        self, cluster: Cluster, node: ClusterNode, raw: dict[str, Any], *, source: str, received_at: datetime
    ) -> bool:
        try:
            report = NodeReport.model_validate(raw)
        except ValidationError as exc:
            log.warning("invalid_node_report", node=node.name, source=source, error=str(exc)[:300])
            return False
        if report.node_name != node.name:
            log.warning("node_report_name_mismatch", node=node.name, reported=report.node_name, source=source)
            return False
        collected = report.collected_at or received_at
        if collected.tzinfo is None:
            collected = collected.replace(tzinfo=UTC)
        collected = min(collected, received_at)
        if node.last_report_at is not None and collected <= node.last_report_at:
            return False
        node.last_report = report.model_dump(mode="json")
        node.last_report_at = collected
        node.report_source = source
        if report.agent_version:
            node.agent_version = report.agent_version
        engine_version = self.platform.registry.database(cluster.engine).engine_version_of(report.engine)
        if engine_version:
            node.engine_version = engine_version
        if report.bootstrap is not None:
            node.bootstrap_status = report.bootstrap.status
            node.bootstrap_message = report.bootstrap.message
        return True

    # ---------------------------------------------------------------- collect

    def collect(
        self,
        session: Session,
        cluster: Cluster,
        nodes: list[ClusterNode],
        account: CloudAccountContext,
        *,
        include_status: bool = True,
    ) -> list[NodeObservation]:
        """Refresh what we know about ``nodes`` from the cloud, then build observations."""
        self.refresh(session, cluster, nodes, account, include_status=include_status)
        return self.observe(nodes)

    def observe(self, nodes: list[ClusterNode]) -> list[NodeObservation]:
        """Observations from what is stored; no cloud call (usable by every process)."""
        now = utcnow()
        return [self.observation(n, now) for n in nodes]

    def refresh(
        self,
        session: Session,
        cluster: Cluster,
        nodes: list[ClusterNode],
        account: CloudAccountContext,
        *,
        include_status: bool = True,
    ) -> None:
        """Read instance status and node reports from the cloud into the node rows. Needs cloud
        access: runs in the monitoring-worker only (docs/adr/0004)."""
        cloud = self.platform.registry.cloud(cluster.cloud_provider)
        now = utcnow()
        placed = [n for n in nodes if n.instance_name]
        if placed and include_status:
            statuses = cloud.get_resource_status(account, node_refs(placed))
            for node in placed:
                if node.name in statuses:
                    node.instance_status = statuses[node.name]
        # Pull reports only for nodes whose agent is not pushing heartbeats.
        heartbeat_fresh = now - timedelta(seconds=self.settings.agent_stale_seconds / 2)
        pull = node_refs(
            [
                n
                for n in placed
                if not (n.report_source == "heartbeat" and n.last_report_at and n.last_report_at >= heartbeat_fresh)
            ]
        )
        if pull:
            pulled = cloud.read_node_reports(account, pull)
            by_name = {n.name: n for n in nodes}
            for name, guest in pulled.items():
                node = by_name.get(name)
                if node is None:
                    continue
                if guest.bootstrap:
                    node.bootstrap_status = str(guest.bootstrap.get("status") or node.bootstrap_status)
                    node.bootstrap_message = guest.bootstrap.get("message")
                if guest.report:
                    self.ingest_report(cluster, node, guest.report, source="guest-attributes", received_at=now)
        session.flush()

    @staticmethod
    def observation(node: ClusterNode, now: datetime) -> NodeObservation:
        report = None
        if node.last_report:
            try:
                report = NodeReport.model_validate(node.last_report)
            except ValidationError:
                report = None
        age = (now - node.last_report_at).total_seconds() if node.last_report_at else None
        return NodeObservation(
            name=node.name,
            ordinal=node.ordinal,
            zone=node.zone,
            role=node.role,
            instance_status=node.instance_status,
            report=report,
            report_age_seconds=age,
            report_source=node.report_source,
            bootstrap_status=node.bootstrap_status,
            bootstrap_message=node.bootstrap_message,
        )

    # --------------------------------------------------------------- evaluate

    def assess(
        self, cluster: Cluster, nodes: list[ClusterNode], observations: list[NodeObservation]
    ) -> HealthAssessment:
        db = self.platform.registry.database(cluster.engine)
        ctx = HealthContext(
            expected_nodes=[n.name for n in nodes],
            high_availability=cluster.high_availability,
            thresholds=self.thresholds,
            now=utcnow(),
        )
        return db.health(ctx, observations)

    def persist(
        self,
        session: Session,
        cluster: Cluster,
        nodes: list[ClusterNode],
        observations: list[NodeObservation],
        assessment: HealthAssessment,
        *,
        detect_failures: bool = True,
        record_sample: bool = True,
    ) -> None:
        now = utcnow()
        db = self.platform.registry.database(cluster.engine)
        previous = ClusterHealth(cluster.health) if cluster.health else ClusterHealth.UNKNOWN
        cluster.health = assessment.state.value
        cluster.health_details = assessment.to_dict()
        cluster.last_health_check_at = now
        metrics = db.metrics(observations, self.settings.agent_stale_seconds)
        cluster.metrics_summary = {**metrics.to_dict(), "updated_at": now.isoformat()}

        for node in nodes:
            result = assessment.node(node.name)
            if result is None:
                continue
            node_previous = NodeHealth(node.health) if node.health else NodeHealth.UNKNOWN
            agent_previous = AgentStatus(node.agent_status) if node.agent_status else AgentStatus.NOT_REPORTED
            node.health = result.state.value
            node.health_reasons = list(result.reasons)
            node.health_warnings = list(result.warnings)
            node.agent_status = result.agent_status.value
            if detect_failures and node_previous != result.state:
                self._node_event(session, cluster, node, node_previous, agent_previous, result.state, result.reasons)

        # UNKNOWN -> HEALTHY is the normal start-up path and not worth an event.
        startup = previous == ClusterHealth.UNKNOWN and assessment.state == ClusterHealth.HEALTHY
        if detect_failures and previous != assessment.state and not startup:
            reason = assessment.reasons[0] if assessment.reasons else ""
            message = f"Cluster health changed from {previous.value} to {assessment.state.value}"
            add_event(
                session,
                cluster,
                "CLUSTER_HEALTH_CHANGED",
                _CLUSTER_SEVERITY[assessment.state],
                f"{message}: {reason}" if reason and assessment.state != ClusterHealth.HEALTHY else message,
                details={"from": previous.value, "to": assessment.state.value, "reasons": assessment.reasons},
            )

        if record_sample:
            engine = metrics.engine
            session.add(
                MetricSample(
                    cluster_id=cluster.id,
                    captured_at=now,
                    values={
                        "cpu_percent": metrics.cpu_percent,
                        "memory_percent": metrics.memory_percent,
                        "disk_percent": metrics.disk_percent,
                        "network_rx_bytes_per_sec": metrics.network_rx_bytes_per_sec,
                        "network_tx_bytes_per_sec": metrics.network_tx_bytes_per_sec,
                        "nodes_reporting": metrics.nodes_reporting,
                        **{k: v for k, v in engine.items() if isinstance(v, (int, float)) or v is None},
                    },
                )
            )
            cutoff = now - timedelta(hours=self.settings.metrics_retention_hours)
            session.execute(
                delete(MetricSample).where(MetricSample.cluster_id == cluster.id, MetricSample.captured_at < cutoff)
            )

    @staticmethod
    def _node_event(
        session: Session,
        cluster: Cluster,
        node: ClusterNode,
        previous: NodeHealth,
        agent_previous: AgentStatus,
        current: NodeHealth,
        reasons: list[str],
    ) -> None:
        if current == NodeHealth.HEALTHY:
            # UNKNOWN -> HEALTHY is a recovery only if the agent had reported before and went quiet.
            if previous == NodeHealth.UNHEALTHY or agent_previous == AgentStatus.STALE:
                add_event(
                    session,
                    cluster,
                    "NODE_RECOVERED",
                    EventSeverity.INFO,
                    f"{node.name} recovered",
                    node_name=node.name,
                )
            return
        if current == NodeHealth.UNKNOWN and previous == NodeHealth.UNKNOWN:
            return
        reason = reasons[0] if reasons else current.value
        kind, severity = (
            ("NODE_UNHEALTHY", EventSeverity.CRITICAL)
            if current == NodeHealth.UNHEALTHY
            else ("NODE_STATUS_UNKNOWN", EventSeverity.WARNING)
        )
        add_event(
            session,
            cluster,
            kind,
            severity,
            f"{node.name}: {reason}",
            node_name=node.name,
            details={"from": previous.value, "to": current.value, "reasons": reasons},
        )
