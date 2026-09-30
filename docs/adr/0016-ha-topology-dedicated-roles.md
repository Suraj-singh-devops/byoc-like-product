# ADR 0016: Highly available topology with dedicated node roles

- Status: Accepted (2026-09-30). Supersedes the "dedicated master nodes: out of scope" line and
  the fixed-role table of [ADR 0011](0011-elasticsearch-topology-and-ha.md); everything else in
  0011 still applies.
- Related: [ADR 0013](0013-environments-and-registered-networks.md) (zones, subnets),
  [ADR 0017](0017-configuration-management.md)

## Context

The product owner asked for Elasticsearch in a highly available, resilient, fault-tolerant
layout with dedicated master, coordinating and data nodes, and a single address for
applications.

## Decision

Two layouts:

| Layout | Nodes | Use |
|---|---|---|
| `combined` (existing) | node-1..N; nodes 1–3 `master, data, ingest`, later nodes `data, ingest` | Small and test clusters; every cluster created before this ADR |
| `dedicated` (new default for HA) | `master-1..3` (`master`), `data-1..N` (`data, ingest`), `coord-1..N` (coordinating only: `node.roles: []`) | Production |

Dedicated layout rules:

- **Masters:** exactly 3, one per zone, on small VMs with a small disk. They elect the master and
  hold cluster state only. The set never changes: masters are never scaled, so the quorum
  (2 of 3) survives the loss of any one zone or node.
- **Data nodes:** at least 2 (default 3), spread round-robin over the zones. Shard allocation
  awareness on the `zone` attribute, **forced** awareness over the cluster's zones (a zone outage
  never piles every copy onto the survivors), and a default of one replica for new indices.
- **Coordinating nodes:** at least 2 (default 2) in different zones. They take client traffic,
  scatter searches and gather results, so data nodes are not overloaded by clients.
- **Endpoint:** an internal TCP load balancer (one private IP, port 9200) in the registered
  subnet in front of the coordinating nodes, with health checks. Without coordinating nodes it
  fronts the data nodes.
- **Groups have their own machine types and disks.** Data and coordinating groups can be scaled up
  independently (scale-down stays out of scope, ADR 0010); masters cannot be scaled.
- **HA** (`high_availability=true`) with the dedicated layout needs 3 zones (ADR 0013), 3 masters,
  ≥ 2 data nodes and ≥ 2 coordinating nodes.

Health semantics (ADR 0008) become role-aware:

| Condition | Engine health |
|---|---|
| 2 of 3 masters up, all data nodes up | HEALTHY (if the cluster is green; yellow on a single data node) |
| One master, data or coordinating node down | DEGRADED |
| Fewer than 2 masters up | UNHEALTHY (no quorum: no writes, no cluster changes) |
| All coordinating nodes down, or a red cluster | UNHEALTHY |

Still out of scope: automatic node replacement, cross-region replication, snapshots, 5-master
clusters, dedicated ingest or ML nodes.

## Consequences

- A minimum HA cluster is 8 VMs (3 small masters, 3 data, 2 coordinating) plus a load balancer.
- The GCP provisioner role needs the internal load balancer permissions (instance groups, health
  checks, backend services, forwarding rules).
- Existing clusters keep the combined layout; there is no automatic conversion.
- The load balancer's private address is reserved first and added to the node certificate, so
  clients verify TLS at the endpoint; the health-check firewall rule targets only the
  load-balanced nodes.
