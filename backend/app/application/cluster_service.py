from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.network_service import network_ref
from app.application.operation_service import (
    audit_started,
    find_idempotent_operation,
    new_operation,
    request_fingerprint,
)
from app.application.platform import Platform
from app.application.preconditions import require_available_network, require_connected_account
from app.application.principal import Principal
from app.application.provisioning.outcome import add_event, finalize_operation, request_cancellation
from app.domain.cluster_spec import COMBINED, DEDICATED, ClusterSpec, EnvironmentRef, NodeGroupSpec, validate_generic
from app.domain.enums import EventSeverity, OperationType
from app.domain.errors import Conflict, NotFound, ValidationFailed
from app.domain.network import NetworkRef, plan_zones
from app.domain.rbac import Permission
from app.domain.states import (
    CloudAccountStatus,
    ClusterHealth,
    ClusterLifecycle,
    NetworkStatus,
    OperationStatus,
    assert_cluster_transition,
)
from app.domain.text import plural
from app.models import CloudAccount, Cluster, ClusterEvent, ClusterNode, Environment, Network, Operation
from app.providers.cloud.base import NodePlacement
from app.repositories import queries


@dataclass
class ClusterCreateInput:
    name: str
    engine: str
    version: str | None
    # Where the cluster runs (docs/adr/0013). The cloud account and region come from the network;
    # when given they must match it.
    environment_id: str
    network_id: str
    machine_type: str
    node_count: int
    storage_gb: int
    storage_type: str
    high_availability: bool
    zone: str | None = None
    cloud_account_id: str | None = None
    region: str | None = None
    # Dedicated layout (docs/adr/0016): group => {count, machine_type, storage_gb}.
    layout: str = COMBINED
    node_groups: dict[str, dict[str, Any]] | None = None
    # Initial configuration overrides (docs/adr/0017).
    config: dict[str, Any] | None = None


@dataclass
class ClusterView:
    cluster: Cluster
    nodes: list[ClusterNode]
    account: CloudAccount | None
    active_operation: Operation | None
    created_by_email: str | None


def placements_from_nodes(nodes: list[ClusterNode], spec: ClusterSpec | None = None) -> list[NodePlacement]:
    def storage(group: str | None) -> int | None:
        found = spec.group(group) if spec is not None and group else None
        return found.storage_gb if found else None

    return [
        NodePlacement(
            name=n.name,
            ordinal=n.ordinal,
            zone=n.zone,
            roles=tuple(r for r in n.role.split(",") if r),
            group=n.node_group,
            machine_type=n.machine_type,
            storage_gb=storage(n.node_group),
        )
        for n in sorted(nodes, key=lambda n: n.ordinal)
    ]


def node_groups_from_input(groups: dict[str, dict[str, Any]] | None) -> tuple[NodeGroupSpec, ...]:
    problems: dict[str, str] = {}
    result = []
    for name, group in (groups or {}).items():
        try:
            result.append(
                NodeGroupSpec(
                    name=name,
                    count=int(group["count"]),
                    machine_type=str(group["machine_type"]).strip(),
                    storage_gb=int(group.get("storage_gb") or 20),
                )
            )
        except (KeyError, TypeError, ValueError):
            problems[f"node_groups.{name}"] = "Give count and machine_type (and optionally storage_gb)."
    if not result:
        problems["node_groups"] = "A dedicated layout needs node groups."
    if problems:
        raise ValidationFailed("The node groups are invalid.", details={"fields": problems})
    return tuple(result)


class ClusterService:
    def __init__(self, session: Session, platform: Platform, principal: Principal) -> None:
        self.session = session
        self.platform = platform
        self.principal = principal
        self.registry = platform.registry

    # ------------------------------------------------------------------ reads

    def list(
        self, include_deleted: bool = False, environment_id: str | None = None
    ) -> list[tuple[Cluster, Operation | None]]:
        self.principal.require(Permission.CLUSTER_READ)
        org = self.principal.organization_id
        env = queries.environment(self.session, org, environment_id).id if environment_id else None
        active = queries.active_operations_by_cluster(self.session, org)
        clusters = queries.clusters(self.session, org, include_deleted=include_deleted, environment_id=env)
        return [(c, active.get(c.id)) for c in clusters]

    def _cluster(self, cluster_id: str, include_deleted: bool = False, for_update: bool = False) -> Cluster:
        self.principal.require(Permission.CLUSTER_READ)
        return queries.cluster(
            self.session,
            self.principal.organization_id,
            cluster_id,
            include_deleted=include_deleted,
            for_update=for_update,
        )

    def get(self, cluster_id: str) -> ClusterView:
        cluster = self._cluster(cluster_id, include_deleted=True)
        account = self.session.get(CloudAccount, cluster.cloud_account_id) if cluster.cloud_account_id else None
        creator = queries.emails_by_user_id(self.session, {cluster.created_by_id} if cluster.created_by_id else set())
        return ClusterView(
            cluster=cluster,
            nodes=queries.active_nodes(self.session, cluster.id),
            account=account,
            active_operation=queries.active_operations_by_cluster(self.session, cluster.organization_id).get(
                cluster.id
            ),
            created_by_email=creator.get(cluster.created_by_id) if cluster.created_by_id else None,
        )

    def nodes(self, cluster_id: str) -> tuple[Cluster, list[ClusterNode]]:
        cluster = self._cluster(cluster_id, include_deleted=True)
        return cluster, queries.active_nodes(self.session, cluster.id)

    def events(self, cluster_id: str, limit: int = 50) -> list[ClusterEvent]:
        cluster = self._cluster(cluster_id, include_deleted=True)
        return list(queries.cluster_events(self.session, cluster.id, limit))

    def health(self, cluster_id: str) -> dict[str, Any]:
        cluster = self._cluster(cluster_id, include_deleted=True)
        details = dict(cluster.health_details or {})
        return {
            "cluster_id": str(cluster.id),
            "lifecycle": cluster.lifecycle_state,
            "status": cluster.health,
            "infrastructure": details.get("infrastructure", ClusterHealth.UNKNOWN.value),
            "engine": details.get("engine", ClusterHealth.UNKNOWN.value),
            "reasons": details.get("reasons", []),
            "warnings": details.get("warnings", []),
            "nodes": details.get("nodes", []),
            "nodes_expected": details.get("nodes_expected", cluster.node_count),
            "nodes_reporting": details.get("nodes_reporting", 0),
            "engine_status": details.get("engine_status"),
            "engine_node_count": details.get("engine_node_count"),
            "checked_at": cluster.last_health_check_at,
        }

    def metrics(self, cluster_id: str, minutes: int) -> dict[str, Any]:
        cluster = self._cluster(cluster_id, include_deleted=True)
        since = datetime.now(UTC) - timedelta(minutes=minutes)
        samples = queries.metric_samples(self.session, cluster.id, since)
        nodes = []
        for node in queries.active_nodes(self.session, cluster.id):
            report = node.last_report or {}
            nodes.append(
                {
                    "name": node.name,
                    "health": node.health,
                    "agent_status": node.agent_status,
                    "reported_at": node.last_report_at,
                    "system": report.get("system", {}),
                    "engine": {
                        k: (report.get("engine") or {}).get(k)
                        for k in ("jvm_heap_percent", "search_rate", "indexing_rate", "shards", "is_master", "version")
                    },
                }
            )
        return {
            "cluster_id": str(cluster.id),
            "current": cluster.metrics_summary or {},
            "nodes": nodes,
            "history": [{"timestamp": s.captured_at, **(s.values or {})} for s in samples],
            "window_minutes": minutes,
        }

    # ----------------------------------------------------------------- writes

    def create(self, data: ClusterCreateInput, idempotency_key: str | None) -> tuple[Cluster, Operation]:
        self.principal.require(Permission.CLUSTER_CREATE)
        org = self.principal.organization_id
        db = self.registry.database(data.engine)
        version = db.resolve_version(data.version)
        payload = {**data.__dict__, "engine": db.engine, "version": version}
        request_hash = request_fingerprint({"operation": "create", **payload})
        existing = find_idempotent_operation(
            self.session, org, idempotency_key, OperationType.CREATE_CLUSTER, request_hash
        )
        if existing is not None:
            cluster = self.session.get(Cluster, existing.cluster_id)
            assert cluster is not None
            return cluster, existing

        environment, network, account = self._placement_records(data)
        cloud = self.registry.descriptor(account.provider)
        ref = network_ref(network)
        zones = plan_zones((network.details or {}).get("zones") or [], data.zone, data.high_availability)
        if data.layout not in (COMBINED, DEDICATED):
            raise ValidationFailed(
                f"Unknown layout {data.layout}.", details={"fields": {"layout": "Use combined or dedicated."}}
            )
        groups = node_groups_from_input(data.node_groups) if data.layout == DEDICATED else ()
        data_group = next((g for g in groups if g.name == "data"), None)
        spec = ClusterSpec(
            name=data.name.strip(),
            engine=db.engine,
            version=version,
            cloud_provider=cloud.name,
            cloud_account_id=str(account.id),
            project_id=account.project_id,
            region=network.region,
            zone=zones[0],
            machine_type=(data_group.machine_type if data_group else data.machine_type).strip(),
            node_count=sum(g.count for g in groups) if groups else data.node_count,
            storage_gb=data_group.storage_gb if data_group else data.storage_gb,
            storage_type=data.storage_type.strip(),
            high_availability=data.high_availability,
            environment=EnvironmentRef(id=str(environment.id), name=environment.name, type=environment.type),
            network=ref,
            zones=tuple(zones),
            layout=data.layout,
            node_groups=groups,
            config=db.merge_config({}, data.config) if data.config else {},
        )
        validate_generic(spec)
        if spec.storage_type not in {s.name for s in cloud.storage_types()}:
            raise ValidationFailed(
                f"Storage type {spec.storage_type} is not supported.",
                details={"fields": {"storage_type": "Unsupported storage type."}},
            )
        # Static check here; the terraform-runner verifies placement in the account (preflight).
        machine = cloud.validate_placement(spec.region, spec.zone, spec.machine_type, zones)
        group_machines = {}
        for group in spec.node_groups:
            try:
                group_machines[group.name] = cloud.validate_placement(spec.region, spec.zone, group.machine_type, zones)
            except ValidationFailed as exc:
                raise ValidationFailed(
                    exc.message, details={"fields": {f"node_groups.{group.name}.machine_type": exc.message}}
                ) from exc
        if len({m.architecture for m in group_machines.values()}) > 1:
            raise ValidationFailed(
                "All node groups must use the same CPU architecture.",
                reason="Every node installs the same Elasticsearch package and VM image.",
                details={
                    "fields": {
                        f"node_groups.{name}.machine_type": f"{m.name} is {m.architecture}."
                        for name, m in group_machines.items()
                    }
                },
            )
        db.validate(spec, machine, group_machines)
        self._check_capacity(network, ref, zones, spec.node_count)
        if queries.cluster_name_taken(self.session, org, spec.name):
            raise Conflict(
                f"A cluster named '{spec.name}' already exists.",
                details={"fields": {"name": "Name already in use."}},
            )

        cluster_id = uuid.uuid4()
        cluster = Cluster(
            id=cluster_id,
            organization_id=org,
            name=spec.name,
            engine=spec.engine,
            engine_version=spec.version,
            cloud_provider=spec.cloud_provider,
            cloud_account_id=account.id,
            environment_id=environment.id,
            network_id=network.id,
            project_id=spec.project_id,
            region=spec.region,
            zone=spec.zone,
            machine_type=spec.machine_type,
            node_count=spec.node_count,
            storage_gb=spec.storage_gb,
            storage_type=spec.storage_type,
            high_availability=spec.high_availability,
            lifecycle_state=ClusterLifecycle.CREATING.value,
            health=ClusterHealth.UNKNOWN.value,
            health_details={},
            metrics_summary={},
            desired_state=spec.to_desired_state(1),
            actual_state={},
            generation=1,
            observed_generation=0,
            resource_prefix=f"{spec.name}-{cluster_id.hex[:4]}",
            created_by_id=self.principal.user_id,
        )
        self.session.add(cluster)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(f"A cluster named '{spec.name}' already exists.") from exc
        op = new_operation(
            self.session,
            principal=self.principal,
            cluster=cluster,
            operation_type=OperationType.CREATE_CLUSTER,
            params={"spec": cluster.desired_state},
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        audit_started(self.session, self.principal, cluster, op)
        add_event(
            self.session,
            cluster,
            "CREATE_REQUESTED",
            EventSeverity.INFO,
            f"Creation of {db.display_name} {version} ({plural(spec.node_count, 'node')}) "
            f"requested by {self.principal.email}",
        )
        self.session.commit()
        self.platform.queue.enqueue(str(op.id))
        return cluster, op

    def _placement_records(self, data: ClusterCreateInput) -> tuple[Environment, Network, CloudAccount]:
        org = self.principal.organization_id
        try:
            environment = queries.environment(self.session, org, data.environment_id)
        except NotFound as exc:
            raise ValidationFailed(
                "The selected environment does not exist.",
                details={"fields": {"environment_id": "Unknown environment."}},
            ) from exc
        try:
            network = queries.network(self.session, org, data.network_id)
        except NotFound as exc:
            raise ValidationFailed(
                "The selected network does not exist.", details={"fields": {"network_id": "Unknown network."}}
            ) from exc
        if network.environment_id != environment.id:
            raise ValidationFailed(
                f"Network '{network.name}' belongs to another environment.",
                details={"fields": {"network_id": f"Choose a network of environment {environment.name}."}},
            )
        if network.status != NetworkStatus.AVAILABLE:
            raise ValidationFailed(
                f"Network '{network.name}' is not available ({network.status}).",
                code="NETWORK_NOT_AVAILABLE",
                suggested_action="Open the environment, fix the network's reported problem and validate it again.",
                details={"fields": {"network_id": "Validate this network first."}},
            )
        account = self.session.get(CloudAccount, network.cloud_account_id)
        assert account is not None
        if data.cloud_account_id and str(data.cloud_account_id) != str(account.id):
            raise ValidationFailed(
                "The cloud account must be the network's.",
                details={"fields": {"cloud_account_id": f"Network {network.name} uses cloud account {account.name}."}},
            )
        if data.region and data.region.strip() != network.region:
            raise ValidationFailed(
                f"Network '{network.name}' is in {network.region}.",
                details={"fields": {"region": f"Must be {network.region}, the network's region."}},
            )
        if account.status != CloudAccountStatus.CONNECTED:
            raise ValidationFailed(
                f"Cloud account '{account.name}' is not connected ({account.status}).",
                suggested_action="Open Cloud accounts, fix any reported problem and run validation again.",
                details={"fields": {"network_id": f"The network's cloud account {account.name} is not connected."}},
            )
        return environment, network, account

    @staticmethod
    def _check_capacity(network: Network, ref: NetworkRef, zones: list[str], node_count: int) -> None:
        """Free addresses in the subnets the nodes will use, from the network's latest lookup."""
        free = {s["id"]: int(s.get("available_ips") or 0) for s in (network.details or {}).get("subnets", [])}
        used = {ref.subnet_for(zone).id for zone in zones}
        available = sum(free.get(subnet, 0) for subnet in used)
        if available < node_count:
            free = plural(available, "free address", "es")
            raise ValidationFailed(
                f"The network's subnets have {free} for {plural(node_count, 'node')}.",
                suggested_action="Use fewer nodes, or register a network with larger subnets.",
                details={"fields": {"node_count": f"At most {available} in this network."}},
            )

    def _ensure_idle(self, cluster: Cluster) -> None:
        active = queries.active_mutation(self.session, cluster.id)
        if active is not None:
            raise Conflict(
                f"Operation {active.operation_type} ({active.status}) is in progress on this cluster.",
                suggested_action="Wait for it to finish, or cancel it, and try again.",
                details={"operation_id": str(active.id)},
            )

    def scale(
        self, cluster_id: str, node_count: int, idempotency_key: str | None, group: str | None = None
    ) -> tuple[Cluster, Operation]:
        self.principal.require(Permission.CLUSTER_SCALE)
        cluster = self._cluster(cluster_id, for_update=True)
        request_hash = request_fingerprint(
            {"operation": "scale", "cluster": str(cluster.id), "node_count": node_count, "group": group}
        )
        existing = find_idempotent_operation(
            self.session, cluster.organization_id, idempotency_key, OperationType.SCALE_CLUSTER, request_hash
        )
        if existing is not None:
            return cluster, existing
        nodes = queries.active_nodes(self.session, cluster.id)
        spec = ClusterSpec.from_desired_state(cluster.desired_state)
        if spec.layout == DEDICATED:
            return self._scale_group(cluster, spec, nodes, group, node_count, idempotency_key, request_hash)
        if node_count < len(nodes):
            raise ValidationFailed(
                f"Scaling down ({len(nodes)} to {node_count} nodes) is not supported.",
                code="SCALE_DOWN_NOT_SUPPORTED",
                reason="Removing nodes needs shard relocation that the platform does not perform yet.",
                suggested_action=f"Choose more than {plural(len(nodes), 'node')}.",
                details={"fields": {"node_count": f"Must be more than {len(nodes)}."}},
            )
        if cluster.lifecycle_state != ClusterLifecycle.ACTIVE:
            raise Conflict(
                f"The cluster is {cluster.lifecycle_state}; only an active cluster can be scaled.",
                suggested_action="Wait for the current operation to finish, or retry the failed one.",
            )
        require_connected_account(self.session, cluster)
        require_available_network(self.session, cluster)
        self._ensure_idle(cluster)
        db = self.registry.database(cluster.engine)
        plan = db.scale(
            spec,
            placements_from_nodes(nodes),
            node_count,
            list(spec.zones) or sorted({n.zone for n in nodes}) or [cluster.zone],
            (cluster.actual_state or {}).get("engine_settings"),
        )
        assert_cluster_transition(ClusterLifecycle(cluster.lifecycle_state), ClusterLifecycle.SCALING)
        cluster.generation += 1
        cluster.node_count = node_count
        cluster.desired_state = spec.with_node_count(node_count).to_desired_state(cluster.generation)
        cluster.lifecycle_state = ClusterLifecycle.SCALING.value
        cluster.status_message = None
        op = new_operation(
            self.session,
            principal=self.principal,
            cluster=cluster,
            operation_type=OperationType.SCALE_CLUSTER,
            params={"from": plan.current_count, "to": plan.target_count},
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        audit_started(self.session, self.principal, cluster, op)
        add_event(
            self.session,
            cluster,
            "SCALE_REQUESTED",
            EventSeverity.INFO,
            f"Scale from {plan.current_count} to {plural(node_count, 'node')} requested by {self.principal.email}",
        )
        self.session.commit()
        self.platform.queue.enqueue(str(op.id))
        return cluster, op

    def _scale_group(
        self,
        cluster: Cluster,
        spec: ClusterSpec,
        nodes: list[ClusterNode],
        group: str | None,
        node_count: int,
        idempotency_key: str | None,
        request_hash: str,
    ) -> tuple[Cluster, Operation]:
        """Dedicated layout (docs/adr/0016): data and coordinating groups grow; masters stay at 3."""
        db = self.registry.database(cluster.engine)
        placements = placements_from_nodes(nodes, spec)
        zones = list(spec.zones) or sorted({n.zone for n in nodes}) or [cluster.zone]
        previous = (cluster.actual_state or {}).get("engine_settings")
        # Validates the group and refuses scale-down before looking at the lifecycle.
        plan = db.scale(spec, placements, node_count, zones, previous, group)
        if cluster.lifecycle_state != ClusterLifecycle.ACTIVE:
            raise Conflict(
                f"The cluster is {cluster.lifecycle_state}; only an active cluster can be scaled.",
                suggested_action="Wait for the current operation to finish, or retry the failed one.",
            )
        require_connected_account(self.session, cluster)
        require_available_network(self.session, cluster)
        self._ensure_idle(cluster)
        target = spec.with_group_count(str(group), node_count)
        self._check_capacity_for(cluster, target.node_count - spec.node_count)
        assert_cluster_transition(ClusterLifecycle(cluster.lifecycle_state), ClusterLifecycle.SCALING)
        cluster.generation += 1
        cluster.node_count = target.node_count
        cluster.desired_state = target.to_desired_state(cluster.generation)
        cluster.lifecycle_state = ClusterLifecycle.SCALING.value
        cluster.status_message = None
        op = new_operation(
            self.session,
            principal=self.principal,
            cluster=cluster,
            operation_type=OperationType.SCALE_CLUSTER,
            params={"from": plan.current_count, "to": plan.target_count, "group": group},
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        audit_started(self.session, self.principal, cluster, op)
        add_event(
            self.session,
            cluster,
            "SCALE_REQUESTED",
            EventSeverity.INFO,
            f"Scale of the {group} group from {plan.current_count} to {plural(node_count, 'node')} "
            f"requested by {self.principal.email}",
        )
        self.session.commit()
        self.platform.queue.enqueue(str(op.id))
        return cluster, op

    def _check_capacity_for(self, cluster: Cluster, added: int) -> None:
        if cluster.network_id is None or added <= 0:
            return
        network = self.session.get(Network, cluster.network_id)
        free = sum(
            int(s.get("available_ips") or 0) for s in ((network.details or {}).get("subnets", []) if network else [])
        )
        if free and added > free:
            raise ValidationFailed(
                f"The network's subnets have {plural(free, 'free address', 'es')} for {plural(added, 'new node')}.",
                details={"fields": {"node_count": f"At most {free} more in this network."}},
            )

    # -------------------------------------------------------------- configuration

    def config(self, cluster_id: str) -> dict[str, Any]:
        cluster = self._cluster(cluster_id, include_deleted=True)
        db = self.registry.database(cluster.engine)
        spec = ClusterSpec.from_desired_state(cluster.desired_state)
        desired = spec.config
        catalog_keys = {s["key"] for s in db.settings_catalog()}
        applied = dict((cluster.actual_state or {}).get("config") or {})
        return {
            "cluster_id": str(cluster.id),
            "settings": db.settings_catalog(),
            "desired": desired,
            "applied": applied,
            "pending": db.config_plan(applied, desired),
            # The rendered configuration file with the desired settings (docs/adr/0018).
            "config_file": db.config_file(spec, desired),
            "custom": {k: v for k, v in desired.items() if k not in catalog_keys},
        }

    def update_config(
        self, cluster_id: str, changes: dict[str, Any], idempotency_key: str | None
    ) -> tuple[Cluster, Operation]:
        """Apply configuration changes (docs/adr/0017): dynamic ones live, static ones by a rolling restart."""
        self.principal.require(Permission.CLUSTER_CONFIGURE)
        cluster = self._cluster(cluster_id, for_update=True)
        request_hash = request_fingerprint({"operation": "config", "cluster": str(cluster.id), "changes": changes})
        existing = find_idempotent_operation(
            self.session, cluster.organization_id, idempotency_key, OperationType.UPDATE_CONFIG, request_hash
        )
        if existing is not None:
            return cluster, existing
        db = self.registry.database(cluster.engine)
        spec = ClusterSpec.from_desired_state(cluster.desired_state)
        merged = db.merge_config(spec.config, changes)
        plan = db.config_plan(spec.config, merged)
        if not plan["dynamic"] and not plan["static"]:
            raise ValidationFailed(
                "The configuration already has these values.",
                code="NO_CHANGE",
                details={"fields": {k: "Unchanged." for k in changes}},
            )
        if cluster.lifecycle_state != ClusterLifecycle.ACTIVE:
            raise Conflict(
                f"The cluster is {cluster.lifecycle_state}; configuration changes need an active cluster.",
                suggested_action="Wait for the current operation to finish, or retry the failed one.",
            )
        require_connected_account(self.session, cluster)
        require_available_network(self.session, cluster)
        self._ensure_idle(cluster)
        assert_cluster_transition(ClusterLifecycle(cluster.lifecycle_state), ClusterLifecycle.UPDATING)
        cluster.generation += 1
        cluster.desired_state = spec.with_config(merged).to_desired_state(cluster.generation)
        cluster.lifecycle_state = ClusterLifecycle.UPDATING.value
        cluster.status_message = None
        op = new_operation(
            self.session,
            principal=self.principal,
            cluster=cluster,
            operation_type=OperationType.UPDATE_CONFIG,
            params={"changes": changes, "dynamic": plan["dynamic"], "static": plan["static"]},
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        audit_started(self.session, self.principal, cluster, op)
        restart = " (rolling restart)" if plan["static"] else ""
        add_event(
            self.session,
            cluster,
            "CONFIG_UPDATE_REQUESTED",
            EventSeverity.INFO,
            f"Configuration change of {', '.join(plan['dynamic'] + plan['static'])}{restart} requested by "
            f"{self.principal.email}",
        )
        self.session.commit()
        self.platform.queue.enqueue(str(op.id))
        return cluster, op

    def delete(self, cluster_id: str, confirm: str | None) -> tuple[Cluster, Operation]:
        """Delete a cluster (docs/adr/0010): typed confirmation, idempotent, pre-empts other work."""
        self.principal.require(Permission.CLUSTER_DELETE)
        cluster = self._cluster(cluster_id, for_update=True)
        if (confirm or "").strip() != cluster.name:
            raise ValidationFailed(
                "Deleting a cluster must be confirmed with its name.",
                code="CONFIRMATION_REQUIRED",
                reason="The cluster, its VMs, disks and data are permanently removed.",
                suggested_action=f"Repeat the request with confirm={cluster.name}.",
                details={"fields": {"confirm": f"Type the cluster name ({cluster.name}) to confirm."}},
            )
        in_progress = queries.active_delete(self.session, cluster.id)
        if in_progress is not None:
            return cluster, in_progress

        previous = ClusterLifecycle(cluster.lifecycle_state)
        assert_cluster_transition(previous, ClusterLifecycle.DELETING)
        cluster.lifecycle_state = ClusterLifecycle.DELETING.value
        cluster.status_message = None

        # Stop everything else first: queued work is cancelled now, running work at its next
        # safe point. The delete itself starts once nothing else is running on the cluster.
        reason = f"Superseded by the deletion requested by {self.principal.email}"
        cancelled, stopping = [], []
        for other in queries.active_operations(self.session, cluster.id):
            request_cancellation(other, by=self.principal, reason=reason)
            if other.status == OperationStatus.PENDING:
                finalize_operation(self.session, other, OperationStatus.CANCELLED, actor=self.principal)
                cancelled.append(str(other.id))
            else:
                stopping.append(str(other.id))

        op = new_operation(
            self.session,
            principal=self.principal,
            cluster=cluster,
            operation_type=OperationType.DELETE_CLUSTER,
            params={"previous_lifecycle": previous.value},
            extra_metadata={
                "preempted": {"cancelled": cancelled, "stopping": stopping},
                "log": (
                    [
                        {
                            "at": datetime.now(UTC).isoformat(),
                            "step": None,
                            "message": f"Waiting for {plural(len(stopping), 'running operation')} to stop",
                        }
                    ]
                    if stopping
                    else []
                ),
            },
        )
        audit_started(self.session, self.principal, cluster, op)
        add_event(
            self.session,
            cluster,
            "DELETE_REQUESTED",
            EventSeverity.INFO,
            f"Deletion requested by {self.principal.email}",
        )
        self.session.commit()
        self.platform.queue.enqueue(str(op.id))
        return cluster, op

    def request_health_check(self, cluster_id: str) -> Operation:
        self.principal.require(Permission.CLUSTER_HEALTH_CHECK)
        cluster = self._cluster(cluster_id)
        if cluster.lifecycle_state not in (ClusterLifecycle.ACTIVE, ClusterLifecycle.SCALING):
            raise Conflict(f"The cluster is {cluster.lifecycle_state}; health checks run on active clusters.")
        op = new_operation(
            self.session,
            principal=self.principal,
            cluster=cluster,
            operation_type=OperationType.HEALTH_CHECK,
            params={},
        )
        audit_started(self.session, self.principal, cluster, op)
        self.session.commit()
        self.platform.queue.enqueue(str(op.id))
        return op
