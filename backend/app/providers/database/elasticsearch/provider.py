from __future__ import annotations

from statistics import fmean
from typing import Any

from app.domain.cluster_spec import ClusterSpec
from app.domain.errors import ValidationFailed
from app.domain.health import (
    HealthAssessment,
    NodeAssessment,
    NodeObservation,
    evaluate_platform_node,
    rollup_infrastructure,
)
from app.domain.states import ClusterHealth, NodeHealth, worst_cluster_health
from app.domain.text import plural
from app.providers.cloud.base import MachineType, NodePlacement
from app.providers.database.base import (
    ClusterMetrics,
    DatabaseProvider,
    EngineCatalog,
    EngineProvisionPlan,
    EngineVersion,
    HealthContext,
    MetricDefinition,
    ScalePlan,
)
from app.providers.database.elasticsearch import topology
from app.providers.database.elasticsearch.versions import CatalogEntry, VersionCatalog, load_catalog

HTTP_PORT = 9200
TRANSPORT_PORT = 9300
MIN_MEMORY_GB = 4
MIN_STORAGE_GB = 20
MAX_NODES = 30
HA_MIN_NODES = 3
HEAP_WARNING_PERCENT = 85
HEAP_CRITICAL_PERCENT = 92

METRICS = (
    MetricDefinition("jvm_heap_percent", "JVM heap", "percent"),
    MetricDefinition("search_rate", "Search rate", "per_second"),
    MetricDefinition("indexing_rate", "Indexing rate", "per_second"),
    MetricDefinition("active_shards", "Active shards", "count"),
    MetricDefinition("unassigned_shards", "Unassigned shards", "count"),
    MetricDefinition("docs_count", "Documents", "count"),
)


def _avg(values: list[float]) -> float | None:
    return round(fmean(values), 1) if values else None


def _version_field_error(message: str, versions: list[str]) -> ValidationFailed:
    choices = ", ".join(versions) or "none"
    return ValidationFailed(
        message,
        details={"fields": {"version": f"Use an exact version from the catalog: {choices}."}},
        suggested_action=f"Choose one of: {choices}.",
    )


class ElasticsearchProvider(DatabaseProvider):
    engine = "elasticsearch"
    display_name = "Elasticsearch"
    aliases = ("elastic search", "elastic-search", "elastic_search", "es")

    def __init__(self, catalog: VersionCatalog | None = None) -> None:
        self.versions = catalog or load_catalog()

    def catalog(self) -> EngineCatalog:
        return EngineCatalog(
            engine=self.engine,
            display_name=self.display_name,
            description="Distributed search and analytics engine.",
            versions=tuple(
                EngineVersion(
                    version=e.version,
                    label=f"{self.display_name} {e.version}",
                    status=e.status,
                    default=e.version == self.versions.default_version,
                    distribution=e.distribution,
                    license_review_status=e.license_review_status,
                    notes=e.notes,
                )
                for e in self.versions.entries
                if e.status != "withdrawn"
            ),
            default_version=self.versions.default_version,
            min_nodes=1,
            max_nodes=MAX_NODES,
            ha_min_nodes=HA_MIN_NODES,
            min_storage_gb=MIN_STORAGE_GB,
            max_storage_gb=65536,
            min_memory_gb=MIN_MEMORY_GB,
            default_machine_type="e2-standard-4",
            ports={"http": HTTP_PORT, "transport": TRANSPORT_PORT},
            metrics=METRICS,
        )

    # ----------------------------------------------------------------- versions

    def resolve_version(self, version: str | None) -> str:
        requested = (version or "").strip()
        if not requested:
            return self.versions.default_version
        entry = self.versions.get(requested)
        supported = [e.version for e in self.versions.supported()]
        if entry is None or entry.status != "supported":
            raise _version_field_error(f"Elasticsearch version '{requested}' is not available.", supported)
        return entry.version

    def installable(self, version: str) -> CatalogEntry:
        """The catalog entry to install for an existing or new cluster (not withdrawn)."""
        entry = self.versions.get(version)
        if entry is None or not entry.installable:
            raise ValidationFailed(
                f"Elasticsearch {version} can no longer be installed.",
                reason="The version is not in the platform's version catalog, or it was withdrawn.",
                suggested_action="Upgrades are not available yet; contact support to plan a migration.",
                details={"fields": {"version": "Not installable."}},
            )
        return entry

    # --------------------------------------------------------------- validation

    def validate(self, spec: ClusterSpec, machine: MachineType | None = None) -> None:
        problems: dict[str, str] = {}
        entry = self.versions.get(spec.version)
        if entry is None or not entry.installable:
            problems["version"] = f"Elasticsearch {spec.version} is not in the version catalog."
        if spec.node_count > MAX_NODES:
            problems["node_count"] = f"Elasticsearch clusters are limited to {MAX_NODES} nodes."
        if spec.high_availability and spec.node_count < HA_MIN_NODES:
            problems["high_availability"] = (
                f"High availability needs at least {HA_MIN_NODES} nodes so that the cluster keeps "
                "a master quorum and replica shards when one node or zone fails."
            )
        if spec.storage_gb < MIN_STORAGE_GB:
            problems["storage_gb"] = f"Elasticsearch nodes need at least {MIN_STORAGE_GB} GB of storage."
        if machine is not None and machine.memory_gb < MIN_MEMORY_GB:
            problems["machine_type"] = (
                f"{machine.name} has {machine.memory_gb:g} GB of memory; Elasticsearch needs at "
                f"least {MIN_MEMORY_GB} GB (half is used for the JVM heap)."
            )
        if problems:
            raise ValidationFailed(
                "The Elasticsearch cluster request is invalid.",
                details={"fields": problems},
                suggested_action="Correct the highlighted fields and submit again.",
            )

    # ------------------------------------------------------------ topology/config

    def provision(self, spec: ClusterSpec, zones: list[str]) -> EngineProvisionPlan:
        nodes = topology.initial_topology(spec.node_count, zones)
        return EngineProvisionPlan(nodes=nodes, settings=self.configure(spec, nodes, None))

    def configure(
        self,
        spec: ClusterSpec,
        nodes: list[NodePlacement],
        previous_settings: dict[str, Any] | None,
    ) -> dict[str, Any]:
        entry = self.installable(spec.version)
        masters = [n.name for n in sorted(nodes, key=lambda n: n.ordinal) if topology.is_master_eligible(n.roles)]
        # cluster.initial_master_nodes must never change after the cluster first forms:
        # nodes added later must join the existing cluster, never bootstrap a new one.
        initial = (previous_settings or {}).get("initial_master_nodes") or masters
        zones = sorted({n.zone for n in nodes})
        return {
            "cluster_name": spec.name,
            "version": entry.version,
            "package": entry.package_settings(),
            "http_port": HTTP_PORT,
            "transport_port": TRANSPORT_PORT,
            "seed_nodes": masters,
            "initial_master_nodes": list(initial),
            "zone_awareness": bool(spec.high_availability and len(zones) > 1),
        }

    def scale(
        self,
        spec: ClusterSpec,
        current_nodes: list[NodePlacement],
        target_count: int,
        zones: list[str],
        previous_settings: dict[str, Any] | None,
    ) -> ScalePlan:
        current = len(current_nodes)
        if target_count < current:
            raise ValidationFailed(
                f"Scaling down ({current} to {target_count} nodes) is not supported.",
                code="SCALE_DOWN_NOT_SUPPORTED",
                reason="Removing nodes needs shard relocation that the platform does not perform yet.",
                suggested_action=f"Choose more than {plural(current, 'node')}.",
                details={"fields": {"node_count": f"Must be more than {current}."}},
            )
        if target_count == current:
            raise ValidationFailed(
                f"The cluster already has {plural(current, 'node')}.",
                code="NO_CHANGE",
                details={"fields": {"node_count": f"The cluster already has {current}."}},
            )
        if target_count > MAX_NODES:
            raise ValidationFailed(
                "The scale request is invalid.",
                details={"fields": {"node_count": f"Elasticsearch clusters are limited to {MAX_NODES} nodes."}},
            )
        ordered = sorted(current_nodes, key=lambda n: n.ordinal)
        next_ordinal = (ordered[-1].ordinal if ordered else 0) + 1
        add = [topology.placement(o, zones) for o in range(next_ordinal, next_ordinal + target_count - current)]
        remaining = ordered + add
        return ScalePlan(
            current_count=current,
            target_count=target_count,
            add=add,
            nodes=remaining,
            settings=self.configure(spec.with_node_count(target_count), remaining, previous_settings),
        )

    # ------------------------------------------------------------------- health

    def health(self, ctx: HealthContext, observations: list[NodeObservation]) -> HealthAssessment:
        stale_after = ctx.thresholds.agent_stale_seconds
        nodes: list[NodeAssessment] = []
        views: list[dict[str, Any]] = []
        reporting = 0

        for obs in observations:
            node = evaluate_platform_node(obs, ctx.thresholds)
            report = obs.fresh_report(stale_after)
            if report is not None and not (obs.vm_down or obs.vm_missing):
                reporting += 1
                es = report.engine or {}
                heap = es.get("jvm_heap_percent")
                if not es.get("reachable"):
                    node.engine_failed(f"Elasticsearch unavailable: {es.get('error') or 'not responding on port 9200'}")
                elif heap is not None and heap >= HEAP_CRITICAL_PERCENT:
                    views.append(es)
                    node.engine_failed(f"JVM heap critical: {heap:.0f}%")
                else:
                    views.append(es)
                    node.engine = NodeHealth.HEALTHY
                    if heap is not None and heap >= HEAP_WARNING_PERCENT:
                        node.warn(f"JVM heap high: {heap:.0f}%")
            nodes.append(node)

        expected = len(ctx.expected_nodes)
        infrastructure, reasons = rollup_infrastructure(nodes, expected)
        warnings: list[str] = [f"{n.name}: {w}" for n in nodes for w in n.warnings]
        engine_states: list[ClusterHealth] = []
        engine_status: str | None = None
        engine_nodes: int | None = None

        # Every node sees the same cluster state; prefer the view of the elected master.
        view = next((v for v in views if v.get("is_master")), views[0] if views else None)
        if view is not None:
            engine_status = view.get("cluster_status")
            engine_nodes = view.get("number_of_nodes")
            if engine_status == "red":
                engine_states.append(ClusterHealth.UNHEALTHY)
                reasons.append(
                    f"Cluster status RED: {view.get('unassigned_shards', 0)} shards unassigned, "
                    "including primaries (some data is unavailable)"
                )
            elif engine_status == "yellow":
                if expected == 1:
                    warnings.append("Single-node cluster: replica shards cannot be allocated (no redundancy)")
                else:
                    engine_states.append(ClusterHealth.DEGRADED)
                    reasons.append(
                        f"Cluster status YELLOW: {view.get('unassigned_shards', 0)} replica shards unassigned"
                    )
            elif engine_status != "green":
                engine_states.append(ClusterHealth.UNHEALTHY)
                reasons.append(f"Elasticsearch cluster has not formed: {view.get('error') or 'no elected master'}")
            if engine_nodes is not None and engine_nodes < expected:
                engine_states.append(ClusterHealth.DEGRADED)
                reasons.append(f"Node missing: {engine_nodes}/{expected} nodes have joined the cluster")

            # Elasticsearch down, or silent agents, on VMs that are still running.
            engine_down = [
                n.name for n in nodes if n.infrastructure != NodeHealth.UNHEALTHY and n.engine == NodeHealth.UNHEALTHY
            ]
            silent = [
                n.name for n in nodes if n.infrastructure != NodeHealth.UNHEALTHY and n.engine == NodeHealth.UNKNOWN
            ]
            if engine_down:
                majority = expected and len(engine_down) * 2 > expected
                engine_states.append(ClusterHealth.UNHEALTHY if majority else ClusterHealth.DEGRADED)
                reasons.append(f"Elasticsearch unhealthy on: {', '.join(engine_down)}")
            if silent:
                engine_states.append(ClusterHealth.DEGRADED)
                reasons.append(f"No recent report from: {', '.join(silent)}")
            engine = worst_cluster_health(engine_states) if engine_states else ClusterHealth.HEALTHY
        elif reporting:
            engine = ClusterHealth.UNHEALTHY
            reasons.append("Elasticsearch is not responding on any node")
        else:
            engine = ClusterHealth.UNKNOWN
            if infrastructure != ClusterHealth.UNHEALTHY:
                reasons.append("No health data received from the nodes yet")

        return HealthAssessment(
            infrastructure=infrastructure,
            engine=engine,
            reasons=reasons,
            warnings=warnings,
            nodes=nodes,
            nodes_expected=expected,
            nodes_reporting=reporting,
            engine_status=engine_status,
            engine_node_count=engine_nodes,
            checked_at=ctx.now,
        )

    # ------------------------------------------------------------------ metrics

    def metrics(self, observations: list[NodeObservation], stale_after: float) -> ClusterMetrics:
        reports = [r for o in observations if (r := o.fresh_report(stale_after)) is not None]
        systems = [r.system for r in reports]
        engines = [r.engine for r in reports if r.engine.get("reachable")]

        disk_used = sum(s.disk_used_bytes or 0 for s in systems)
        disk_total = sum(s.disk_total_bytes or 0 for s in systems)
        if disk_total:
            disk_percent: float | None = round(100 * disk_used / disk_total, 1)
        else:
            disk_percent = _avg([s.disk_percent for s in systems if s.disk_percent is not None])

        def total(values: list[float | None]) -> float | None:
            present = [v for v in values if v is not None]
            return round(sum(present), 1) if present else None

        view = next((e for e in engines if e.get("is_master")), engines[0] if engines else {})
        engine = {
            "cluster_status": view.get("cluster_status"),
            "jvm_heap_percent": _avg([e["jvm_heap_percent"] for e in engines if e.get("jvm_heap_percent") is not None]),
            "search_rate": total([e.get("search_rate") for e in engines]),
            "indexing_rate": total([e.get("indexing_rate") for e in engines]),
            "active_shards": view.get("active_shards"),
            "unassigned_shards": view.get("unassigned_shards"),
            "docs_count": view.get("docs_count"),
        }
        return ClusterMetrics(
            node_count=len(observations),
            nodes_reporting=len(reports),
            cpu_percent=_avg([s.cpu_percent for s in systems if s.cpu_percent is not None]),
            memory_percent=_avg([s.memory_percent for s in systems if s.memory_percent is not None]),
            disk_percent=disk_percent,
            disk_used_bytes=disk_used or None,
            disk_total_bytes=disk_total or None,
            network_rx_bytes_per_sec=total([s.network_rx_bytes_per_sec for s in systems]),
            network_tx_bytes_per_sec=total([s.network_tx_bytes_per_sec for s in systems]),
            engine=engine,
        )
