"""Registered networks and node placement (docs/adr/0013).

A cluster runs in a network the customer registered: an existing VPC and subnet(s). The parts
a cluster needs (VPC, subnets, their ranges and zones) are copied into its desired state at
creation, so the cluster never depends on later reads of the network record.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.errors import ValidationFailed

# High availability means surviving the loss of one zone (docs/adr/0011): three
# master-eligible nodes in three distinct zones.
HA_ZONES = 3


@dataclass(frozen=True)
class SubnetRef:
    # GCP: projects/<project>/regions/<region>/subnetworks/<name>; AWS: subnet-...
    id: str
    cidr: str
    # AWS availability zone; None for a regional subnet (GCP), which serves every zone.
    zone: str | None = None

    def to_doc(self) -> dict[str, Any]:
        return {"id": self.id, "cidr": self.cidr, "zone": self.zone}

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> SubnetRef:
        return cls(id=str(doc["id"]), cidr=str(doc["cidr"]), zone=doc.get("zone"))


@dataclass(frozen=True)
class NetworkRef:
    """The network a cluster runs in, as copied into its desired state."""

    id: str
    name: str
    # GCP: projects/<project>/global/networks/<name>; AWS: vpc-...
    vpc: str
    subnets: tuple[SubnetRef, ...]

    def subnet_for(self, zone: str) -> SubnetRef:
        for subnet in self.subnets:
            if subnet.zone is None or subnet.zone == zone:
                return subnet
        raise ValueError(f"Network {self.name} has no subnet in zone {zone}")

    @property
    def cidrs(self) -> list[str]:
        return [s.cidr for s in self.subnets]

    def to_doc(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "vpc": self.vpc, "subnets": [s.to_doc() for s in self.subnets]}

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> NetworkRef:
        return cls(
            id=str(doc["id"]),
            name=str(doc["name"]),
            vpc=str(doc["vpc"]),
            subnets=tuple(SubnetRef.from_doc(s) for s in doc["subnets"]),
        )


def plan_zones(available: Sequence[str], preferred: str | None, high_availability: bool) -> list[str]:
    """Zones for a new cluster: one zone, or three distinct zones for HA with the preferred first."""
    zones = list(dict.fromkeys(z for z in available if z))
    if not zones:
        raise ValidationFailed(
            "The network has no zone where nodes can run.",
            suggested_action="Validate the network again, or register another one.",
            details={"fields": {"network_id": "No usable zone."}},
        )
    primary = (preferred or "").strip() or zones[0]
    if primary not in zones:
        raise ValidationFailed(
            f"Zone {primary} is not available in this network.",
            details={"fields": {"zone": f"Choose one of {', '.join(zones)}."}},
        )
    if not high_availability:
        return [primary]
    others = sorted(z for z in zones if z != primary)
    if len(others) < HA_ZONES - 1:
        raise ValidationFailed(
            f"High availability needs {HA_ZONES} zones; this network has {len(zones)}.",
            reason=(
                f"The network's subnets are in {', '.join(zones)}. Three master-eligible nodes in fewer "
                "zones do not survive the loss of a zone."
            ),
            suggested_action=(
                f"Register a network with subnets in {HA_ZONES} availability zones, or turn high availability off."
            ),
            details={"fields": {"high_availability": f"Needs subnets in {HA_ZONES} zones."}},
        )
    return [primary, *others[: HA_ZONES - 1]]
