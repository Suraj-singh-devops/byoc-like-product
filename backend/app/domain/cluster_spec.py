"""Desired state of a cluster.

The spec is engine- and cloud-agnostic. It is persisted as ``clusters.desired_state`` in the
document format below, and the control plane reconciles the actual state towards it::

    cluster: {name}
    engine: {type, version}
    cloud: {provider, accountId, project, region, zone, zones}
    environment: {id, name, type}
    network: {id, name, vpc, subnets: [{id, cidr, zone}]}
    layout: combined | dedicated
    nodeGroups: [{name, count, machineType, storageGB}]    (dedicated layout, docs/adr/0016)
    config: {setting: value}                              (overrides, docs/adr/0017)
    compute: {machineType}
    storage: {sizeGB, type}
    nodes: {count}
    highAvailability: {enabled}

``project`` is the GCP project ID or the AWS account ID. Clusters created before environments
and networks (docs/adr/0013) have no ``environment``, ``network`` or ``zones``; they run in a
dedicated VPC and derive their zones from the region.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Any

from app.domain.errors import ValidationFailed
from app.domain.network import NetworkRef

# 3-40 chars, lowercase letters/digits/hyphens, starts with a letter. The name becomes part
# of cloud resource names, which are limited to 63 characters.
CLUSTER_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")
# Generic shapes only; each cloud provider checks that its regions and zones exist
# (GCP: asia-south1, asia-south1-a; AWS: ap-south-1, ap-south-1a).
LOCATION_RE = re.compile(r"^[a-z][a-z0-9-]{1,48}[a-z0-9]$")
# GCP e2-standard-8, AWS m6i.2xlarge
MACHINE_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,62}$")

MAX_NODES = 50
MIN_STORAGE_GB = 10
MAX_STORAGE_GB = 65536


@dataclass(frozen=True)
class EnvironmentRef:
    id: str
    name: str
    type: str

    def to_doc(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "type": self.type}

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> EnvironmentRef:
        return cls(id=str(doc["id"]), name=str(doc["name"]), type=str(doc["type"]))


@dataclass(frozen=True)
class NodeGroupSpec:
    """A group of nodes with one role in a dedicated layout (the engine defines the groups)."""

    name: str
    count: int
    machine_type: str
    storage_gb: int

    def to_doc(self) -> dict[str, Any]:
        return {"name": self.name, "count": self.count, "machineType": self.machine_type, "storageGB": self.storage_gb}

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> NodeGroupSpec:
        return cls(
            name=str(doc["name"]),
            count=int(doc["count"]),
            machine_type=str(doc["machineType"]),
            storage_gb=int(doc["storageGB"]),
        )


COMBINED = "combined"
DEDICATED = "dedicated"


@dataclass(frozen=True)
class ClusterSpec:
    name: str
    engine: str
    version: str
    cloud_provider: str
    cloud_account_id: str
    project_id: str
    region: str
    zone: str
    machine_type: str
    node_count: int
    storage_gb: int
    storage_type: str
    high_availability: bool
    environment: EnvironmentRef | None = None
    network: NetworkRef | None = None
    # Zones chosen at creation (the first is ``zone``); nodes are placed round-robin over them.
    zones: tuple[str, ...] = ()
    # combined: every node runs the same roles (node_count, machine_type, storage_gb).
    # dedicated: node_groups, each with its own count, machine type and disk; node_count is their
    # total and machine_type/storage_gb describe the data group.
    layout: str = COMBINED
    node_groups: tuple[NodeGroupSpec, ...] = ()
    # Configuration overrides (setting => value), validated by the engine's settings catalog.
    config: dict[str, Any] = dataclasses.field(default_factory=dict)

    def with_node_count(self, node_count: int) -> ClusterSpec:
        return dataclasses.replace(self, node_count=node_count)

    def group(self, name: str) -> NodeGroupSpec | None:
        return next((g for g in self.node_groups if g.name == name), None)

    def with_group_count(self, name: str, count: int) -> ClusterSpec:
        groups = tuple(dataclasses.replace(g, count=count) if g.name == name else g for g in self.node_groups)
        return dataclasses.replace(self, node_groups=groups, node_count=sum(g.count for g in groups))

    def with_config(self, config: dict[str, Any]) -> ClusterSpec:
        return dataclasses.replace(self, config=dict(config))

    def to_desired_state(self, generation: int) -> dict[str, Any]:
        cloud: dict[str, Any] = {
            "provider": self.cloud_provider,
            "accountId": self.cloud_account_id,
            "project": self.project_id,
            "region": self.region,
            "zone": self.zone,
        }
        if self.zones:
            cloud["zones"] = list(self.zones)
        doc: dict[str, Any] = {
            "cluster": {"name": self.name},
            "engine": {"type": self.engine, "version": self.version},
            "cloud": cloud,
            "compute": {"machineType": self.machine_type},
            "storage": {"sizeGB": self.storage_gb, "type": self.storage_type},
            "nodes": {"count": self.node_count},
            "highAvailability": {"enabled": self.high_availability},
            "generation": generation,
        }
        if self.environment is not None:
            doc["environment"] = self.environment.to_doc()
        if self.network is not None:
            doc["network"] = self.network.to_doc()
        if self.layout != COMBINED:
            doc["layout"] = self.layout
            doc["nodeGroups"] = [g.to_doc() for g in self.node_groups]
        if self.config:
            doc["config"] = dict(self.config)
        return doc

    @classmethod
    def from_desired_state(cls, doc: dict[str, Any]) -> ClusterSpec:
        try:
            return cls(
                name=doc["cluster"]["name"],
                engine=doc["engine"]["type"],
                version=doc["engine"]["version"],
                cloud_provider=doc["cloud"]["provider"],
                cloud_account_id=doc["cloud"]["accountId"],
                project_id=doc["cloud"]["project"],
                region=doc["cloud"]["region"],
                zone=doc["cloud"]["zone"],
                machine_type=doc["compute"]["machineType"],
                node_count=int(doc["nodes"]["count"]),
                storage_gb=int(doc["storage"]["sizeGB"]),
                storage_type=doc["storage"]["type"],
                high_availability=bool(doc["highAvailability"]["enabled"]),
                environment=EnvironmentRef.from_doc(doc["environment"]) if doc.get("environment") else None,
                network=NetworkRef.from_doc(doc["network"]) if doc.get("network") else None,
                zones=tuple(doc["cloud"].get("zones") or ()),
                layout=str(doc.get("layout") or COMBINED),
                node_groups=tuple(NodeGroupSpec.from_doc(g) for g in doc.get("nodeGroups") or ()),
                config=dict(doc.get("config") or {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailed(f"Desired state document is malformed: {exc}") from exc


def validate_generic(spec: ClusterSpec) -> None:
    """Checks every engine/cloud combination must pass. Engines add their own rules."""
    problems: dict[str, str] = {}
    if not CLUSTER_NAME_RE.match(spec.name):
        problems["name"] = (
            "Must be 3-40 characters of lowercase letters, digits and hyphens, "
            "start with a letter and not end with a hyphen."
        )
    if not LOCATION_RE.match(spec.region):
        problems["region"] = f"'{spec.region}' is not a valid region name."
    if not LOCATION_RE.match(spec.zone):
        problems["zone"] = f"'{spec.zone}' is not a valid zone name."
    elif not spec.zone.startswith(spec.region):
        problems["zone"] = f"Zone '{spec.zone}' is not in region '{spec.region}'."
    if spec.zones and spec.zones[0] != spec.zone:
        problems["zone"] = f"Zone '{spec.zone}' must be the first placement zone."
    if not MACHINE_TYPE_RE.match(spec.machine_type):
        problems["machine_type"] = f"'{spec.machine_type}' is not a valid machine type name."
    if not 1 <= spec.node_count <= MAX_NODES:
        problems["node_count"] = f"Must be between 1 and {MAX_NODES}."
    if not MIN_STORAGE_GB <= spec.storage_gb <= MAX_STORAGE_GB:
        problems["storage_gb"] = f"Must be between {MIN_STORAGE_GB} and {MAX_STORAGE_GB} GB."
    if problems:
        raise ValidationFailed(
            "The cluster request is invalid.",
            details={"fields": problems},
            suggested_action="Correct the highlighted fields and submit again.",
        )
