from __future__ import annotations

import json
from statistics import fmean
from typing import Any

from app.domain.cluster_spec import DEDICATED, ClusterSpec
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
from app.providers.database.elasticsearch import settings as es_settings
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
# Dedicated layout (docs/adr/0016).
MASTERS = 3
HA_MIN_DATA = 2
HA_MIN_COORDINATING = 2
NODE_GROUPS = (
    {
        "name": "master",
        "label": "Master nodes",
        "min": MASTERS,
        "max": MASTERS,
        "ha_min": MASTERS,
        "default_count": 3,
        "default_storage_gb": 20,
        "scalable": False,
        "description": "Elect the master and hold the cluster state; one per zone.",
    },
    {
        "name": "data",
        "label": "Data nodes",
        "min": 1,
        "max": MAX_NODES,
        "ha_min": HA_MIN_DATA,
        "default_count": 3,
        "default_storage_gb": 500,
        "scalable": True,
        "description": "Hold the shards; replicas are kept in another zone.",
    },
    {
        "name": "coordinating",
        "label": "Coordinating nodes",
        "min": 0,
        "max": 10,
        "ha_min": HA_MIN_COORDINATING,
        "default_count": 2,
        "default_storage_gb": 20,
        "scalable": True,
        "description": "Take client requests behind the internal load balancer.",
    },
)

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
            node_groups=NODE_GROUPS,
            settings=tuple(self.settings_catalog()),
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

    def validate(
        self,
        spec: ClusterSpec,
        machine: MachineType | None = None,
        group_machines: dict[str, MachineType] | None = None,
    ) -> None:
        problems: dict[str, str] = {}
        entry = self.versions.get(spec.version)
        if entry is None or not entry.installable:
            problems["version"] = f"Elasticsearch {spec.version} is not in the version catalog."
        if spec.layout == DEDICATED:
            problems.update(self._validate_groups(spec, group_machines or {}))
        elif spec.node_count > MAX_NODES:
            problems["node_count"] = f"Elasticsearch clusters are limited to {MAX_NODES} nodes."
        if spec.layout != DEDICATED and spec.high_availability and spec.node_count < HA_MIN_NODES:
            problems["high_availability"] = (
                f"High availability needs at least {HA_MIN_NODES} nodes so that the cluster keeps "
                "a master quorum and replica shards when one node or zone fails."
            )
        if spec.layout != DEDICATED and spec.storage_gb < MIN_STORAGE_GB:
            problems["storage_gb"] = f"Elasticsearch nodes need at least {MIN_STORAGE_GB} GB of storage."
        if spec.layout != DEDICATED and machine is not None and machine.memory_gb < MIN_MEMORY_GB:
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

    @staticmethod
    def _validate_groups(spec: ClusterSpec, machines: dict[str, MachineType]) -> dict[str, str]:
        problems: dict[str, str] = {}
        groups = {g.name: g for g in spec.node_groups}
        for name in groups:
            if name not in topology.GROUPS:
                problems[f"node_groups.{name}"] = f"Unknown node group; use {', '.join(topology.GROUPS)}."
        master, data = groups.get(topology.MASTER), groups.get(topology.DATA)
        coordinating = groups.get(topology.COORDINATING)
        if master is None or master.count != MASTERS:
            problems["node_groups.master.count"] = (
                f"A dedicated layout has exactly {MASTERS} master nodes, one per zone, so a quorum survives the "
                "loss of any one."
            )
        if data is None or data.count < 1:
            problems["node_groups.data.count"] = "At least one data node is needed."
        elif spec.high_availability and data.count < HA_MIN_DATA:
            problems["node_groups.data.count"] = (
                f"High availability needs at least {HA_MIN_DATA} data nodes, so every shard has a copy in another zone."
            )
        if spec.high_availability and (coordinating is None or coordinating.count < HA_MIN_COORDINATING):
            problems["node_groups.coordinating.count"] = (
                f"High availability needs at least {HA_MIN_COORDINATING} coordinating nodes behind the load balancer."
            )
        if sum(g.count for g in groups.values()) > MAX_NODES:
            problems["node_count"] = f"Elasticsearch clusters are limited to {MAX_NODES} nodes."
        for name, group in groups.items():
            if group.storage_gb < MIN_STORAGE_GB:
                problems[f"node_groups.{name}.storage_gb"] = f"At least {MIN_STORAGE_GB} GB."
            machine = machines.get(name)
            if machine is not None and machine.memory_gb < MIN_MEMORY_GB:
                problems[f"node_groups.{name}.machine_type"] = (
                    f"{machine.name} has {machine.memory_gb:g} GB of memory; Elasticsearch needs at least "
                    f"{MIN_MEMORY_GB} GB."
                )
        return problems

    # ------------------------------------------------------------ topology/config

    def provision(self, spec: ClusterSpec, zones: list[str]) -> EngineProvisionPlan:
        if spec.layout == DEDICATED:
            nodes = topology.dedicated_topology(spec.node_groups, zones)
        else:
            nodes = topology.initial_topology(spec.node_count, zones)
        return EngineProvisionPlan(nodes=nodes, settings=self.configure(spec, nodes, None))

    def load_balancer_targets(self, spec: ClusterSpec, nodes: list[NodePlacement]) -> list[str]:
        """Dedicated layout: the coordinating nodes, or the data nodes when there are none."""
        if spec.layout != DEDICATED:
            return []
        for group in (topology.COORDINATING, topology.DATA):
            names = sorted((n.name for n in nodes if n.group == group), key=lambda n: int(n.rsplit("-", 1)[1]))
            if names:
                return names
        return []

    # ---------------------------------------------------------- configuration

    def settings_catalog(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in es_settings.CATALOG]

    def merge_config(self, current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
        return es_settings.merge_config(current, changes)

    def config_plan(self, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        return {
            "dynamic": [k for k in changed if es_settings.scope_of(k) == es_settings.DYNAMIC],
            "static": [k for k in changed if es_settings.scope_of(k) == es_settings.STATIC],
        }

    def config_file(self, spec: ClusterSpec, config: dict[str, Any]) -> dict[str, Any]:
        """elasticsearch.yml per node group: the lines the platform owns, then the user's settings."""
        _, static = es_settings.split(config)
        static.pop(es_settings.HEAP_PERCENT, None)
        user = [f"{key}: {json.dumps(value)}" for key, value in sorted(static.items())]
        if spec.layout == DEDICATED:
            groups = [(g.name, list(topology.GROUP_ROLES[g.name])) for g in spec.node_groups]
        else:
            groups = [("all", ["master", "data", "ingest"])]
        files = []
        for name, roles in groups:
            managed = [
                f'cluster.name: "{spec.name}"',
                'node.name: "<node name>"',
                f"node.roles: {json.dumps(roles)}",
                'node.attr.zone: "<zone>"',
                "path.data: /var/lib/elasticsearch",
                "path.logs: /var/log/elasticsearch",
                'network.host: ["_local_", "<private IP>"]',
                "http.port: 9200",
                "transport.port: 9300",
                "discovery.seed_hosts: [<master nodes>:9300]",
            ]
            if spec.high_availability:
                managed.append("cluster.routing.allocation.awareness.attributes: zone")
                if spec.layout == DEDICATED:
                    managed.append("cluster.routing.allocation.awareness.force.zone.values: [<cluster zones>]")
            managed += [
                "xpack.security.enabled: true",
                "xpack.security.transport.ssl.*: <cluster certificates>",
                "xpack.security.http.ssl.*: <cluster certificates>",
            ]
            files.append({"group": name, "managed": managed, "user": user})
        return {
            "path": "/etc/elasticsearch/elasticsearch.yml",
            "files": files,
            "reserved_prefixes": list(es_settings.RESERVED_PREFIXES),
        }

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
        dynamic, static = es_settings.split(spec.config)
        heap_percent = int(static.pop(es_settings.HEAP_PERCENT, es_settings.BY_KEY[es_settings.HEAP_PERCENT].default))
        dedicated_ha = spec.layout == DEDICATED and spec.high_availability and len(zones) > 1
        return {
            "layout": spec.layout,
            # Live settings, applied by the agent on the elected master (docs/adr/0017).
            "cluster_settings": dynamic,
            "cluster_settings_hash": es_settings.settings_hash(dynamic),
            # Node settings and heap, applied by a rolling restart.
            "node_settings": static,
            "heap_percent": heap_percent,
            # Forced awareness: a zone outage never piles every copy onto the surviving zones.
            "forced_awareness_zones": zones if dedicated_ha else [],
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
        group: str | None = None,
    ) -> ScalePlan:
        if spec.layout == DEDICATED:
            return self._scale_group(spec, current_nodes, target_count, zones, previous_settings, group)
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

    def _scale_group(
        self,
        spec: ClusterSpec,
        current_nodes: list[NodePlacement],
        target_count: int,
        zones: list[str],
        previous_settings: dict[str, Any] | None,
        group_name: str | None,
    ) -> ScalePlan:
        group = spec.group(group_name or "")
        if group is None or group_name not in (topology.DATA, topology.COORDINATING):
            raise ValidationFailed(
                "Choose the data or the coordinating group to scale.",
                reason=f"Master nodes stay at {MASTERS} so that the quorum never changes.",
                details={"fields": {"group": "Must be data or coordinating."}},
            )
        current = sum(1 for n in current_nodes if n.group == group.name)
        label = f"{group.name} node"
        if target_count < current:
            raise ValidationFailed(
                f"Scaling down ({current} to {target_count} {group.name} nodes) is not supported.",
                code="SCALE_DOWN_NOT_SUPPORTED",
                reason="Removing nodes needs shard relocation that the platform does not perform yet.",
                details={"fields": {"node_count": f"Must be more than {current}."}},
            )
        if target_count == current:
            raise ValidationFailed(
                f"The {group.name} group already has {plural(current, label)}.",
                code="NO_CHANGE",
                details={"fields": {"node_count": f"The group already has {current}."}},
            )
        if len(current_nodes) - current + target_count > MAX_NODES:
            raise ValidationFailed(
                "The scale request is invalid.",
                details={"fields": {"node_count": f"Elasticsearch clusters are limited to {MAX_NODES} nodes."}},
            )
        add = topology.add_to_group(group, sorted(current_nodes, key=lambda n: n.ordinal), target_count, zones)
        remaining = sorted(current_nodes, key=lambda n: n.ordinal) + add
        target_spec = spec.with_group_count(group.name, target_count)
        return ScalePlan(
            current_count=current,
            target_count=target_count,
            add=add,
            nodes=remaining,
            settings=self.configure(target_spec, remaining, previous_settings),
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
        groups = ctx.node_groups
        data_nodes = sum(1 for g in groups.values() if g == topology.DATA) if groups else expected
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
                if data_nodes == 1:
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
            if groups:
                engine_states.extend(self._role_health(groups, nodes, reasons))
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

    @staticmethod
    def _role_health(groups: dict[str, str], nodes: list[NodeAssessment], reasons: list[str]) -> list[ClusterHealth]:
        """Dedicated layout (docs/adr/0016): the master quorum and the coordinating tier."""
        states: list[ClusterHealth] = []
        down = {n.name for n in nodes if NodeHealth.UNHEALTHY in (n.infrastructure, n.engine)}
        masters = [name for name, group in groups.items() if group == topology.MASTER]
        masters_up = sum(1 for m in masters if m not in down)
        if masters and masters_up * 2 <= len(masters):
            states.append(ClusterHealth.UNHEALTHY)
            reasons.append(
                f"Master quorum lost: {masters_up} of {len(masters)} masters available (no writes, no cluster changes)"
            )
        coordinating = [name for name, group in groups.items() if group == topology.COORDINATING]
        if coordinating and all(c in down for c in coordinating):
            states.append(ClusterHealth.UNHEALTHY)
            reasons.append("No coordinating node available: the cluster endpoint cannot serve requests")
        return states

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
