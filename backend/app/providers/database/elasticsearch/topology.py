"""Node naming, roles and zone placement for Elasticsearch clusters.

* Nodes are named node-1..node-N; the ordinal never changes for a node.
* The first three nodes are master-eligible (``master,data,ingest``); further nodes are
  ``data,ingest``. Scaling down removes the highest ordinals first, so for clusters of three
  or more nodes the master-eligible set is never touched.
* With high availability, nodes are spread round-robin over up to three zones and shard
  allocation awareness keeps a primary and its replicas in different zones.
"""

from __future__ import annotations

from app.providers.cloud.base import NodePlacement

MASTER_ELIGIBLE_COUNT = 3
MASTER_ROLES = ("master", "data", "ingest")
DATA_ROLES = ("data", "ingest")


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
