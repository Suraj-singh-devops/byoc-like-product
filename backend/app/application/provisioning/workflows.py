"""Operation workflows, run by the cluster-manager. Each step is idempotent so a failed or
abandoned run can be retried from the beginning: Terraform converges on the same infrastructure,
and waits re-check state.

The cluster-manager has no cloud access (docs/adr/0004). Cloud work is a task: preflight, plan,
apply and destroy run in the terraform-runner; node refreshes run in the monitoring-worker. The
workflows read node state only from the database.

Workflows change the cluster lifecycle only out of the state their operation put it in
(``lifecycle_change``), so a workflow finishing after a delete was requested leaves the
cluster in DELETING.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.application.cluster_service import placements_from_nodes
from app.application.health_service import HealthService
from app.application.platform import Platform
from app.application.provisioning.context import OperationContext, StepDef
from app.application.provisioning.outcome import add_event
from app.application.task_handlers import request_to_dict
from app.domain.cluster_spec import ClusterSpec, validate_generic
from app.domain.enums import BOOTSTRAP_ORDER, AgentCommandStatus, BootstrapStatus, EventSeverity
from app.domain.errors import ProvisioningError
from app.domain.health import HealthAssessment
from app.domain.states import (
    ClusterHealth,
    ClusterLifecycle,
    NodeHealth,
    NodeLifecycle,
    OperationStatus,
    lifecycle_change,
)
from app.domain.text import plural
from app.infrastructure.db import session_scope
from app.models import AgentCommand, CloudAccount, Cluster, ClusterNode
from app.providers.cloud.base import (
    InfrastructureRequest,
    InfrastructureState,
    MachineType,
    NodePlacement,
    ProvisionedNode,
)
from app.providers.cloud.descriptor import CloudDescriptor
from app.providers.database.base import DatabaseProvider
from app.repositories import queries

S = OperationStatus
VM_DOWN = ("TERMINATED", "STOPPED", "SUSPENDED", "NOT_FOUND")


@dataclass
class Loaded:
    cluster_id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    resource_prefix: str
    spec: ClusterSpec
    account_id: uuid.UUID
    db: DatabaseProvider
    cloud: CloudDescriptor
    actual_state: dict[str, Any]


def _bootstrap_rank(status: str | None) -> int:
    try:
        return BOOTSTRAP_ORDER.get(BootstrapStatus(status or "pending"), 0)
    except ValueError:
        return 0


class BaseWorkflow:
    def __init__(self, ctx: OperationContext, platform: Platform, health: HealthService) -> None:
        self.ctx = ctx
        self.platform = platform
        self.health = health
        self.settings = platform.settings
        self.sf = platform.session_factory
        self.simulated = False

    def run(self) -> dict[str, Any]:
        raise NotImplementedError

    # ---------------------------------------------------------------- helpers

    def load(self) -> Loaded:
        with session_scope(self.sf) as s:
            cluster = s.get(Cluster, self.ctx.cluster_id)
            if cluster is None or cluster.deleted_at is not None:
                raise ProvisioningError("The cluster no longer exists.", code="CLUSTER_NOT_FOUND")
            account = s.get(CloudAccount, cluster.cloud_account_id) if cluster.cloud_account_id else None
            if account is None:
                raise ProvisioningError(
                    "The cluster's cloud account was removed.",
                    code="CLOUD_ACCOUNT_MISSING",
                    suggested_action="Re-add the cloud account for this project.",
                )
            cloud = self.platform.registry.descriptor(cluster.cloud_provider)
            self.simulated = cloud.simulated
            return Loaded(
                cluster_id=cluster.id,
                organization_id=cluster.organization_id,
                name=cluster.name,
                resource_prefix=cluster.resource_prefix,
                spec=ClusterSpec.from_desired_state(cluster.desired_state),
                account_id=account.id,
                db=self.platform.registry.database(cluster.engine),
                cloud=cloud,
                actual_state=dict(cluster.actual_state or {}),
            )

    @property
    def poll_interval(self) -> float:
        if self.simulated:
            return max(0.05, self.settings.mock_speed)
        return 10.0

    def infra_request(
        self, loaded: Loaded, nodes: list[NodePlacement], settings: dict[str, Any]
    ) -> InfrastructureRequest:
        spec = loaded.spec
        return InfrastructureRequest(
            cluster_id=str(loaded.cluster_id),
            organization_id=str(loaded.organization_id),
            resource_prefix=loaded.resource_prefix,
            engine=spec.engine,
            engine_version=spec.version,
            project_id=spec.project_id,
            region=spec.region,
            zone=spec.zone,
            machine_type=spec.machine_type,
            storage_gb=spec.storage_gb,
            storage_type=spec.storage_type,
            high_availability=spec.high_availability,
            nodes=sorted(nodes, key=lambda n: n.ordinal),
            engine_settings=settings,
            labels={
                "managed-by": "byoc",
                "byoc-cluster-id": str(loaded.cluster_id),
                "byoc-org-id": str(loaded.organization_id),
                "byoc-engine": spec.engine,
            },
            network=spec.network,
        )

    def zones(self, loaded: Loaded) -> list[str]:
        """Zones chosen at creation; clusters created before registered networks derive them."""
        spec = loaded.spec
        if spec.zones:
            return list(spec.zones)
        return loaded.cloud.placement_zones(spec.region, spec.zone, spread=spec.high_availability)

    # ------------------------------------------------------------ cloud tasks

    def task(self, loaded: Loaded, kind: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        return self.platform.tasks.run(
            kind,
            {"account_id": str(loaded.account_id), **payload},
            timeout=timeout,
            organization_id=loaded.organization_id,
            operation_id=self.ctx.operation_id,
            on_message=self.ctx.message,
            on_poll=self.ctx.heartbeat,
        )

    def preflight(self, loaded: Loaded) -> MachineType:
        """In the terraform-runner, as the provisioning identity: access, the network, placement."""
        spec = loaded.spec
        result = self.task(
            loaded,
            "preflight",
            {
                "region": spec.region,
                "zone": spec.zone,
                "machine_type": spec.machine_type,
                "network": spec.network.to_doc() if spec.network else None,
            },
            timeout=self.settings.preflight_timeout_seconds,
        )
        return MachineType(**result["machine"])

    def terraform(self, loaded: Loaded, kind: str, request: InfrastructureRequest) -> dict[str, Any]:
        return self.task(
            loaded, kind, {"request": request_to_dict(request)}, timeout=self.settings.provision_timeout_seconds
        )

    def plan(self, loaded: Loaded, request: InfrastructureRequest) -> None:
        result = self.terraform(loaded, "plan", request)
        self.ctx.result["plan"] = {k: result[k] for k in ("add", "change", "destroy")}
        self.ctx.message(result["summary"])

    def apply(self, loaded: Loaded, request: InfrastructureRequest) -> InfrastructureState:
        result = self.terraform(loaded, "apply", request)
        return InfrastructureState(
            nodes=[ProvisionedNode(**n) for n in result["nodes"]],
            resources=list(result.get("resources") or []),
            outputs=dict(result.get("outputs") or {}),
        )

    def refresh(self, loaded: Loaded) -> None:
        """Ask the monitoring-worker to read the nodes' status and reports into the database."""
        self.task(
            loaded,
            "refresh_cluster",
            {"cluster_id": str(loaded.cluster_id)},
            timeout=self.settings.refresh_timeout_seconds,
        )

    def merge_actual_state(self, **fields: Any) -> None:
        with session_scope(self.sf) as s:
            cluster = s.get(Cluster, self.ctx.cluster_id)
            assert cluster is not None
            cluster.actual_state = {**(cluster.actual_state or {}), **fields}

    def register_nodes(self, placements: list[NodePlacement], state: InfrastructureState) -> None:
        provisioned = {n.name: n for n in state.nodes}
        with session_scope(self.sf) as s:
            existing = {n.name: n for n in queries.active_nodes(s, self.ctx.cluster_id)}
            for placement in placements:
                vm = provisioned.get(placement.name)
                node = existing.get(placement.name)
                if node is None:
                    node = ClusterNode(
                        cluster_id=self.ctx.cluster_id,
                        name=placement.name,
                        ordinal=placement.ordinal,
                        zone=placement.zone,
                        role=",".join(placement.roles),
                        lifecycle_state=NodeLifecycle.BOOTSTRAPPING.value,
                        health=NodeHealth.UNKNOWN.value,
                        health_reasons=[],
                        health_warnings=[],
                    )
                    s.add(node)
                if vm is not None:
                    node.instance_name = vm.instance_name
                    node.instance_id = vm.instance_id
                    node.private_ip = vm.private_ip
                    node.hostname = vm.hostname
                    node.zone = vm.zone
                    node.instance_status = "RUNNING"

    def bootstrapping_nodes(self) -> list[str]:
        with session_scope(self.sf) as s:
            return [
                n.name
                for n in queries.active_nodes(s, self.ctx.cluster_id)
                if n.lifecycle_state == NodeLifecycle.BOOTSTRAPPING
            ]

    def store_infrastructure(self, state: InfrastructureState) -> None:
        self.merge_actual_state(infrastructure=state.outputs, resources=len(state.resources) or None)

    def wait_for_bootstrap(self, loaded: Loaded, names: list[str], until: BootstrapStatus, verb: str) -> None:
        if not names:
            return
        deadline = time.monotonic() + self.settings.bootstrap_timeout_seconds
        target = BOOTSTRAP_ORDER[until]
        while True:
            self.refresh(loaded)
            with session_scope(self.sf) as s:
                cluster = s.get(Cluster, self.ctx.cluster_id)
                assert cluster is not None
                nodes = [n for n in queries.active_nodes(s, cluster.id) if n.name in names]
                snapshot = [
                    (n.name, n.bootstrap_status or "pending", n.bootstrap_message, n.instance_status) for n in nodes
                ]
            for name, status, message, _ in snapshot:
                if status == BootstrapStatus.FAILED:
                    raise ProvisioningError(
                        f"Bootstrap failed on {name}.",
                        code="BOOTSTRAP_FAILED",
                        reason=message or "The startup script reported a failure.",
                        suggested_action=(
                            "Inspect the VM's serial console output (startup script log), fix the cause and "
                            "retry the operation."
                        ),
                    )
            for name, _, _, instance_status in snapshot:
                if instance_status in VM_DOWN:
                    raise ProvisioningError(
                        f"The VM for {name} is {instance_status}.",
                        code="VM_NOT_RUNNING",
                        suggested_action="Check the instance in the cloud console and retry the operation.",
                    )
            done = [name for name, status, _, _ in snapshot if _bootstrap_rank(status) >= target]
            detail = ", ".join(f"{name}: {status}" for name, status, _, _ in snapshot)
            self.ctx.message(f"{len(done)}/{len(names)} nodes {verb} ({detail})")
            if len(done) == len(names):
                return
            if time.monotonic() > deadline:
                pending = [name for name, *_ in snapshot if name not in done]
                raise ProvisioningError(
                    "Timed out waiting for the VMs to bootstrap.",
                    code="BOOTSTRAP_TIMEOUT",
                    reason=f"Still waiting for {', '.join(pending)}.",
                    suggested_action=(
                        "Check that the VMs can reach the package repository through the network's NAT and inspect the "
                        "serial console log, then retry."
                    ),
                )
            self.ctx.sleep(self.poll_interval)

    def wait_for_health(self, loaded: Loaded, expected_nodes: int) -> HealthAssessment:
        deadline = time.monotonic() + self.settings.health_timeout_seconds
        while True:
            self.refresh(loaded)
            with session_scope(self.sf) as s:
                cluster = s.get(Cluster, self.ctx.cluster_id)
                assert cluster is not None
                nodes = queries.active_nodes(s, cluster.id)
                observations = self.health.observe(nodes)
                assessment = self.health.assess(cluster, nodes, observations)
                self.health.persist(s, cluster, nodes, observations, assessment, detect_failures=False)
            joined = assessment.engine_node_count or 0
            if assessment.state == ClusterHealth.HEALTHY and joined >= expected_nodes:
                self.ctx.message(f"Cluster is {assessment.engine_status}: {joined}/{expected_nodes} nodes joined")
                return assessment
            reason = f" - {assessment.reasons[0]}" if assessment.reasons else ""
            self.ctx.message(f"Waiting for the cluster: {joined}/{expected_nodes} nodes joined{reason}")
            if time.monotonic() > deadline:
                raise ProvisioningError(
                    "The cluster did not become healthy in time.",
                    code="HEALTH_CHECK_TIMEOUT",
                    reason="; ".join(assessment.reasons[:3]) or assessment.state.value,
                    suggested_action="Check node health on the cluster page, fix the cause and retry the operation.",
                )
            self.ctx.sleep(self.poll_interval)

    def finalize_cluster(
        self,
        assessment: HealthAssessment,
        expected: ClusterLifecycle,
        event_type: str,
        message: str,
    ) -> None:
        with session_scope(self.sf) as s:
            cluster = s.get(Cluster, self.ctx.cluster_id)
            assert cluster is not None
            nodes = queries.active_nodes(s, cluster.id)
            for node in nodes:
                if node.lifecycle_state == NodeLifecycle.BOOTSTRAPPING:
                    node.lifecycle_state = NodeLifecycle.ACTIVE.value
            versions = sorted({n.engine_version for n in nodes if n.engine_version})
            new = lifecycle_change(cluster.lifecycle_state, expected, ClusterLifecycle.ACTIVE)
            if new is not None:
                cluster.lifecycle_state = new.value
                cluster.status_message = None
            cluster.observed_generation = cluster.generation
            cluster.actual_state = {
                **(cluster.actual_state or {}),
                "engine": {"type": cluster.engine, "version": versions[0] if len(versions) == 1 else versions},
                "nodes": {
                    "count": len(nodes),
                    "members": [
                        {
                            "name": n.name,
                            "zone": n.zone,
                            "privateIp": n.private_ip,
                            "roles": n.role.split(","),
                            "instance": n.instance_name,
                        }
                        for n in nodes
                    ],
                },
                "health": assessment.state.value,
                "observedGeneration": cluster.generation,
                "observedAt": datetime.now(UTC).isoformat(),
            }
            add_event(s, cluster, event_type, EventSeverity.INFO, message)


class CreateClusterWorkflow(BaseWorkflow):
    def run(self) -> dict[str, Any]:
        loaded = self.load()
        spec, db, cloud = loaded.spec, loaded.db, loaded.cloud
        engine = db.display_name
        self.ctx.define_steps(
            [
                StepDef("validate", "Validate request", S.VALIDATING),
                StepDef("validate_cloud", f"Validate {cloud.display_name} access", S.VALIDATING),
                StepDef("plan", "Generate Terraform configuration and plan", S.PROVISIONING),
                StepDef("apply", "Provision infrastructure (Terraform apply)", S.PROVISIONING),
                StepDef("bootstrap", f"Bootstrap VMs and install {engine} {spec.version}", S.BOOTSTRAPPING),
                StepDef("configure", f"Configure and start {engine}", S.CONFIGURING),
                StepDef("health", "Health check", S.HEALTH_CHECK),
                StepDef("register", "Register nodes", S.HEALTH_CHECK),
            ]
        )
        with self.ctx.step("validate"):
            validate_generic(spec)
            db.validate(spec)
            self.ctx.message(
                f"{engine} {spec.version}: {spec.node_count} x {spec.machine_type}, {spec.storage_gb} GB "
                f"{spec.storage_type} per node, high availability {'on' if spec.high_availability else 'off'}"
            )
        with self.ctx.step("validate_cloud"):
            machine = self.preflight(loaded)
            db.validate(spec, machine)
            zones = self.zones(loaded)
            self.ctx.message(f"Access verified for {spec.project_id}; nodes will run in {', '.join(zones)}")

        plan = db.provision(spec, zones)
        request = self.infra_request(loaded, plan.nodes, plan.settings)
        self.merge_actual_state(engine_settings=plan.settings)
        with self.ctx.step("plan"):
            self.plan(loaded, request)
        with self.ctx.step("apply"):
            state = self.apply(loaded, request)
            self.register_nodes(plan.nodes, state)
            self.store_infrastructure(state)
            self.ctx.message(
                f"{plural(len(state.nodes), 'VM')} running: "
                + ", ".join(f"{n.name} {n.private_ip}" for n in state.nodes)
            )
        names = [n.name for n in plan.nodes]
        with self.ctx.step("bootstrap"):
            self.wait_for_bootstrap(loaded, names, BootstrapStatus.CONFIGURING, "installed")
        with self.ctx.step("configure"):
            self.wait_for_bootstrap(loaded, names, BootstrapStatus.READY, "running")
        with self.ctx.step("health"):
            assessment = self.wait_for_health(loaded, len(names))
        with self.ctx.step("register"):
            status = assessment.engine_status or "unknown"
            detail = (
                " (single node: replica shards cannot be allocated)" if len(names) == 1 and status == "yellow" else ""
            )
            self.finalize_cluster(
                assessment,
                ClusterLifecycle.CREATING,
                "CLUSTER_READY",
                f"{engine} cluster is ready with {plural(len(names), 'node')}, cluster status {status}{detail}",
            )
            self.ctx.message(f"Registered {plural(len(names), 'node')}; cluster is {assessment.state.value}")
        self.ctx.result.update({"nodes": names, "health": assessment.state.value})
        return self.ctx.result


class ScaleClusterWorkflow(BaseWorkflow):
    """Scale-up only (docs/adr/0010). Safe to retry: nodes a previous attempt already created
    are kept, and only nodes still bootstrapping are waited for."""

    def run(self) -> dict[str, Any]:
        loaded = self.load()
        spec, db = loaded.spec, loaded.db
        target = int(self.ctx.params["to"])
        with session_scope(self.sf) as s:
            current = placements_from_nodes(queries.active_nodes(s, loaded.cluster_id))
        zones = sorted({n.zone for n in current}) or [spec.zone]
        if spec.zones or spec.high_availability:
            zones = self.zones(loaded)
        previous_settings = loaded.actual_state.get("engine_settings")
        if len(current) < target:
            plan = db.scale(spec, current, target, zones, previous_settings)
            nodes, added, settings = plan.nodes, plan.add, plan.settings
        else:
            nodes, added = current, []
            settings = db.configure(spec, current, previous_settings)

        self.ctx.define_steps(
            [
                StepDef("validate", "Validate request", S.VALIDATING),
                StepDef("plan", "Terraform plan", S.PROVISIONING),
                StepDef("apply", "Terraform apply", S.PROVISIONING),
                StepDef(
                    "bootstrap", f"Bootstrap new VMs and install {db.display_name} {spec.version}", S.BOOTSTRAPPING
                ),
                StepDef("configure", f"Join new nodes to the {db.display_name} cluster", S.CONFIGURING),
                StepDef("health", "Validate cluster health", S.HEALTH_CHECK),
            ]
        )
        with self.ctx.step("validate"):
            self.preflight(loaded)
            change = f"adding {', '.join(n.name for n in added)}" if added else "finishing the nodes already created"
            self.ctx.message(f"Scaling from {self.ctx.params.get('from')} to {target} nodes: {change}")

        request = self.infra_request(loaded, nodes, settings)
        with self.ctx.step("plan"):
            self.plan(loaded, request)
        with self.ctx.step("apply"):
            state = self.apply(loaded, request)
            self.register_nodes(added, state)
            self.store_infrastructure(state)
            self.merge_actual_state(engine_settings=settings)
        new_nodes = self.bootstrapping_nodes()
        with self.ctx.step("bootstrap"):
            self.wait_for_bootstrap(loaded, new_nodes, BootstrapStatus.CONFIGURING, "installed")
        with self.ctx.step("configure"):
            self.wait_for_bootstrap(loaded, new_nodes, BootstrapStatus.READY, "running")
        with self.ctx.step("health"):
            assessment = self.wait_for_health(loaded, target)
            self.finalize_cluster(
                assessment,
                ClusterLifecycle.SCALING,
                "CLUSTER_SCALED",
                f"Scaled from {self.ctx.params.get('from')} to {plural(target, 'node')}",
            )
        self.ctx.result.update({"from": self.ctx.params.get("from"), "to": target, "added": new_nodes})
        return self.ctx.result


class DeleteClusterWorkflow(BaseWorkflow):
    def run(self) -> dict[str, Any]:
        loaded = self.load()
        self.ctx.define_steps(
            [
                StepDef("validate", "Validate request", S.VALIDATING),
                StepDef("destroy", "Destroy infrastructure (Terraform destroy)", S.PROVISIONING),
                StepDef("cleanup", "Remove cluster registration", S.PROVISIONING),
            ]
        )
        with self.ctx.step("validate"):
            for note in loaded.db.delete(loaded.spec):
                self.ctx.message(note)
            self.ctx.message(f"Deleting {loaded.name} and all of its cloud resources, including data disks")
        with self.ctx.step("destroy"):
            with session_scope(self.sf) as s:
                placements = placements_from_nodes(queries.active_nodes(s, loaded.cluster_id))
            settings = loaded.actual_state.get("engine_settings")
            if not placements or not settings:
                fallback = loaded.db.provision(loaded.spec, [loaded.spec.zone])
                placements = placements or fallback.nodes
                settings = settings or fallback.settings
            request = self.infra_request(loaded, placements, settings)
            self.terraform(loaded, "destroy", request)
        with self.ctx.step("cleanup"):
            now = datetime.now(UTC)
            with session_scope(self.sf) as s:
                cluster = s.get(Cluster, loaded.cluster_id)
                assert cluster is not None
                for node in queries.active_nodes(s, cluster.id):
                    node.lifecycle_state = NodeLifecycle.DELETED.value
                    node.deleted_at = now
                    node.agent_token_hash = None
                for command in s.scalars(
                    select(AgentCommand).where(
                        AgentCommand.cluster_id == cluster.id,
                        AgentCommand.status.in_([AgentCommandStatus.PENDING, AgentCommandStatus.SENT]),
                    )
                ):
                    command.status = AgentCommandStatus.EXPIRED.value
                new = lifecycle_change(cluster.lifecycle_state, ClusterLifecycle.DELETING, ClusterLifecycle.DELETED)
                if new is None:
                    raise ProvisioningError(
                        f"The cluster is {cluster.lifecycle_state}, not DELETING.", code="UNEXPECTED_LIFECYCLE"
                    )
                cluster.lifecycle_state = new.value
                cluster.deleted_at = now
                cluster.health = ClusterHealth.UNKNOWN.value
                cluster.metrics_summary = {}
                cluster.status_message = None
                add_event(
                    s, cluster, "CLUSTER_DELETED", EventSeverity.INFO, "Cluster and its cloud resources were deleted"
                )
            self.ctx.message("Cluster deleted")
        return {"deleted": True}


class HealthCheckWorkflow(BaseWorkflow):
    def run(self) -> dict[str, Any]:
        loaded = self.load()
        self.ctx.define_steps([StepDef("health", "Collect node reports and evaluate health", S.HEALTH_CHECK)])
        with self.ctx.step("health"):
            self.refresh(loaded)
            with session_scope(self.sf) as s:
                cluster = s.get(Cluster, loaded.cluster_id)
                assert cluster is not None
                nodes = queries.active_nodes(s, cluster.id)
                observations = self.health.observe(nodes)
                assessment = self.health.assess(cluster, nodes, observations)
                self.health.persist(s, cluster, nodes, observations, assessment)
            summary = "; ".join(assessment.reasons[:3])
            self.ctx.message(f"{assessment.state.value}" + (f": {summary}" if summary else ""))
        return assessment.to_dict()


WORKFLOWS: dict[str, type[BaseWorkflow]] = {
    "CREATE_CLUSTER": CreateClusterWorkflow,
    "SCALE_CLUSTER": ScaleClusterWorkflow,
    "DELETE_CLUSTER": DeleteClusterWorkflow,
    "HEALTH_CHECK": HealthCheckWorkflow,
}
