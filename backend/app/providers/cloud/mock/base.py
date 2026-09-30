"""Shared behaviour of the MOCK_MODE cloud providers (simulated GCP and AWS).

* Simulates Terraform plan, apply and destroy resource by resource, with realistic delays
  scaled by MOCK_SPEED, on top of the simulated data plane (VMs, bootstrap, Elasticsearch,
  agents), so every control-plane path runs as it would against a real cloud.
* Never touches a cloud. Deterministic triggers in identifiers simulate customer mistakes
  (see docs/mock-mode.md).
"""

from __future__ import annotations

import hashlib
import ipaddress
import shutil
import time
import uuid
import zlib
from abc import abstractmethod
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.domain.errors import ProvisioningError
from app.infrastructure.db import SessionFactory
from app.providers.cloud.base import (
    CloudAccountContext,
    CloudProvider,
    GuestReport,
    InfrastructureRequest,
    InfrastructureState,
    MachineType,
    NodePlacement,
    NodeRef,
    PlanSummary,
    ProgressReporter,
    ProvisionedNode,
    ResourceChange,
)
from app.providers.cloud.descriptor import CloudDescriptor
from app.providers.cloud.mock.dataplane import MockDataPlane


def stable_digest(*parts: str) -> str:
    return hashlib.sha256("/".join(parts).encode()).hexdigest()


class SimulatedCloudProvider(CloudProvider):
    # Terraform addresses of each node's resources, formatted with {node}.
    node_resources: tuple[str, ...]

    def __init__(
        self,
        settings: Settings,
        session_factory: SessionFactory,
        descriptor: CloudDescriptor,
        dataplane: MockDataPlane | None = None,
    ) -> None:
        self.settings = settings
        self.descriptor = descriptor
        self.dataplane = dataplane or MockDataPlane(settings, session_factory)

    def _pause(self, seconds: float, progress: ProgressReporter | None = None) -> None:
        duration = seconds * self.settings.mock_speed
        if duration <= 0:
            if progress is not None:
                progress.check_cancelled()
            return
        if progress is not None:
            progress.sleep(duration)
        else:
            time.sleep(duration)

    # ---------------------------------------------------------- provider hooks

    @abstractmethod
    def foundation(self, request: InfrastructureRequest) -> tuple[tuple[str, str], ...]:
        """(Terraform address, progress message) of the resources created once per cluster."""

    @abstractmethod
    def teardown_messages(self, request: InfrastructureRequest) -> tuple[str, ...]: ...

    @abstractmethod
    def node_messages(self, request: InfrastructureRequest, node: NodePlacement) -> tuple[str, str]:
        """Progress messages for creating a node's data disk and its VM."""

    @abstractmethod
    def instance_id(self, project_id: str, zone: str, instance_name: str) -> str: ...

    @abstractmethod
    def hostname(self, request: InfrastructureRequest, zone: str, instance_name: str, private_ip: str) -> str: ...

    @abstractmethod
    def outputs(self, request: InfrastructureRequest, nodes: list[ProvisionedNode]) -> dict[str, Any]: ...

    def render_workspace(self, account: CloudAccountContext, request: InfrastructureRequest) -> None:
        """Write the Terraform that would run, where modules exist for this cloud."""

    # ------------------------------------------------------------------ catalog

    def list_machine_types(self, account: CloudAccountContext, zone: str) -> list[MachineType]:
        return self.descriptor.list_machine_types(zone)

    def validate_placement(
        self, account: CloudAccountContext, region: str, zone: str, machine_type: str
    ) -> MachineType:
        return self.descriptor.validate_placement(region, zone, machine_type)

    def region_zones(self, region: str) -> list[str]:
        return self.descriptor.region_zones(region)

    # ------------------------------------------------------------------ terraform

    def _existing(self, request: InfrastructureRequest) -> dict[str, Any]:
        return {i.node_name: i for i in self.dataplane.instances(request.cluster_id)}

    def plan_infrastructure(
        self, account: CloudAccountContext, request: InfrastructureRequest, progress: ProgressReporter
    ) -> PlanSummary:
        self.render_workspace(account, request)
        progress.message("Initializing Terraform (simulated)")
        self._pause(1.0, progress)
        existing = self._existing(request)
        desired = {n.name for n in request.nodes}
        to_create = sorted(desired - set(existing), key=lambda n: int(n.rsplit("-", 1)[-1]))
        to_remove = sorted(set(existing) - desired)
        if to_remove:
            raise ProvisioningError(
                "Refusing to apply: the plan would destroy data on an existing node.",
                code="UNSAFE_PLAN",
                reason=f"Nodes {', '.join(to_remove)} would be destroyed.",
                suggested_action="Nothing was changed. Check for drift before retrying.",
            )
        summary = PlanSummary()
        if not existing:
            for address, _ in self.foundation(request):
                summary.changes.append(ResourceChange(address, ["create"]))
        for node in to_create:
            for address in self.node_resources:
                summary.changes.append(ResourceChange(address.format(node=node), ["create"]))
        summary.to_add = sum(1 for c in summary.changes if "create" in c.actions)
        progress.message("Planning infrastructure changes (simulated)")
        self._pause(1.2, progress)
        return summary

    def private_ip(self, request: InfrastructureRequest, node: NodePlacement) -> str | None:
        """An address in the node's subnet, spread by cluster so clusters sharing a subnet differ."""
        if request.network is None:
            return None
        subnet = ipaddress.ip_network(request.network.subnet_for(node.zone).cidr, strict=False)
        slots = max(1, (subnet.num_addresses - 16) // 8)
        offset = 8 + (zlib.crc32(request.cluster_id.encode()) % slots) * 8 + node.ordinal
        return str(subnet[min(offset, subnet.num_addresses - 2)])

    def apply_infrastructure(
        self, account: CloudAccountContext, request: InfrastructureRequest, progress: ProgressReporter
    ) -> InfrastructureState:
        existing = self._existing(request)
        if not existing:
            for _, message in self.foundation(request):
                progress.message(message)
                self._pause(0.9, progress)
        machine = next((m for m in self.descriptor.machine_types if m.name == request.machine_type), None)
        for node in sorted(request.nodes, key=lambda n: n.ordinal):
            if node.name in existing:
                continue
            disk_message, vm_message = self.node_messages(request, node)
            progress.message(disk_message)
            self._pause(0.5, progress)
            progress.message(vm_message)
            self._pause(1.0, progress)
            instance_name = request.instance_name(node.name)
            ip = self.private_ip(request, node)
            self.dataplane.create_instance(
                cluster_id=request.cluster_id,
                project_id=request.project_id,
                zone=node.zone,
                name=instance_name,
                node_name=node.name,
                version=request.engine_version,
                private_ip=ip,
                labels={
                    **request.labels,
                    "cluster_name": request.engine_settings.get("cluster_name", ""),
                    "storage_gb": request.storage_gb,
                    "memory_gb": machine.memory_gb if machine else 16,
                    **({"hostname": self.hostname(request, node.zone, instance_name, ip)} if ip else {}),
                },
            )
        return self._state(request)

    def _state(self, request: InfrastructureRequest) -> InfrastructureState:
        nodes = [
            ProvisionedNode(
                name=i.node_name,
                instance_name=i.name,
                instance_id=self.instance_id(i.project_id, i.zone, i.name),
                zone=i.zone,
                private_ip=i.private_ip,
                hostname=self.hostname(request, i.zone, i.name, i.private_ip),
            )
            for i in self.dataplane.instances(request.cluster_id)
        ]
        return InfrastructureState(
            nodes=nodes,
            resources=[address for address, _ in self.foundation(request)],
            outputs=self.outputs(request, nodes),
        )

    def destroy_infrastructure(
        self, account: CloudAccountContext, request: InfrastructureRequest, progress: ProgressReporter
    ) -> None:
        instances = self.dataplane.instances(request.cluster_id)
        progress.message("Planning destruction of all cluster resources (simulated)")
        self._pause(1.0, progress)
        for instance in instances:
            progress.message(f"Destroying VM {instance.name} and its data disk")
            self._pause(0.8, progress)
            self.dataplane.delete_instance(request.project_id, instance.zone, instance.name)
        for message in self.teardown_messages(request):
            progress.message(message)
            self._pause(0.8, progress)
        self.dataplane.delete_cluster(request.cluster_id)
        shutil.rmtree(Path(self.settings.workspaces_dir) / request.cluster_id, ignore_errors=True)

    # ------------------------------------------------------------------ status

    def get_resource_status(self, account: CloudAccountContext, nodes: list[NodeRef]) -> dict[str, str]:
        return self.dataplane.statuses(account.project_id, nodes)

    def read_node_reports(self, account: CloudAccountContext, nodes: list[NodeRef]) -> dict[str, GuestReport]:
        return self.dataplane.reports(account.project_id, nodes)


def uuid_instance_id(project_id: str, zone: str, instance_name: str) -> str:
    """GCE-style numeric instance ID."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{project_id}/{zone}/{instance_name}").int % 10**19)
