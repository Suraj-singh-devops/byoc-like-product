"""Node naming, roles and zone placement for Elasticsearch clusters.

Combined layout (docs/adr/0011): nodes node-1..N; the first three are master-eligible
(``master,data,ingest``), later nodes are ``data,ingest``. The master-eligible set never changes.

Dedicated layout (docs/adr/0016): groups of nodes with one role each, placed round-robin over
the cluster's zones:

* master-1..3        ``master``            elect the master and hold cluster state
* data-1..N          ``data,ingest``       hold shards (awareness keeps copies in other zones)
* coord-1..N         coordinating only     take client traffic behind the internal load balancer

Ordinals are unique across the cluster and never reused; names count within each group.
"""

from __future__ import annotations

from app.domain.cluster_spec import NodeGroupSpec
from app.providers.cloud.base import NodePlacement

MASTER_ELIGIBLE_COUNT = 3
MASTER_ROLES = ("master", "data", "ingest")
DATA_ROLES = ("data", "ingest")

MASTER, DATA, COORDINATING = "master", "data", "coordinating"
GROUPS = (MASTER, DATA, COORDINATING)
GROUP_ROLES: dict[str, tuple[str, ...]] = {MASTER: ("master",), DATA: DATA_ROLES, COORDINATING: ()}
GROUP_PREFIX = {MASTER: "master", DATA: "data", COORDINATING: "coord"}


def node_name(ordinal: int) -> str:
    return f"node-{ordinal}"


def roles_for(ordinal: int) -> tuple[str, ...]:
    return MASTER_ROLES if ordinal <= MASTER_ELIGIBLE_COUNT else DATA_ROLES


def is_master_eligible(roles: tuple[str, ...] | list[str]) -> bool:
    return "master" in roles


def placement(ordinal: int, zones: list[str]) -> NodePlacement:
    if not zones:
        raise ValueError("At least one zone is required")
    return NodePlacement(
        name=node_name(ordinal),
        ordinal=ordinal,
        zone=zones[(ordinal - 1) % len(zones)],
        roles=roles_for(ordinal),
    )


def initial_topology(node_count: int, zones: list[str]) -> list[NodePlacement]:
    return [placement(i, zones) for i in range(1, node_count + 1)]


# ------------------------------------------------------------------- dedicated


def group_placement(group: NodeGroupSpec, index: int, ordinal: int, zones: list[str]) -> NodePlacement:
    """The ``index``-th node (1-based) of a group, with a cluster-wide ``ordinal``."""
    if not zones:
        raise ValueError("At least one zone is required")
    return NodePlacement(
        name=f"{GROUP_PREFIX[group.name]}-{index}",
        ordinal=ordinal,
        zone=zones[(index - 1) % len(zones)],
        roles=GROUP_ROLES[group.name],
        group=group.name,
        machine_type=group.machine_type,
        storage_gb=group.storage_gb,
    )


def dedicated_topology(groups: tuple[NodeGroupSpec, ...], zones: list[str]) -> list[NodePlacement]:
    by_name = {g.name: g for g in groups}
    nodes: list[NodePlacement] = []
    for name in GROUPS:
        group = by_name.get(name)
        for index in range(1, (group.count if group else 0) + 1):
            nodes.append(group_placement(group, index, len(nodes) + 1, zones))  # type: ignore[arg-type]
    return nodes


def add_to_group(
    group: NodeGroupSpec, current: list[NodePlacement], target: int, zones: list[str]
) -> list[NodePlacement]:
    """Placements for the new nodes when ``group`` grows to ``target`` nodes."""
    existing = [n for n in current if n.group == group.name]
    next_ordinal = max((n.ordinal for n in current), default=0) + 1
    return [
        group_placement(group, index, next_ordinal + offset, zones)
        for offset, index in enumerate(range(len(existing) + 1, target + 1))
    ]
