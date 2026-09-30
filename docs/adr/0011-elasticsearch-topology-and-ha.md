# ADR 0011: Elasticsearch topology and high availability in the MVP

- Status: Accepted
- Date: 2026-09-27
- Related: PRD v2 §7 (no production-grade HA automation), §8 (HA option); TRD v2 §11, §23, §25

## Context

The create form offers an HA option, while full HA automation (automatic node replacement,
self-healing, multi-region) is out of the MVP. The HA option therefore needs a precise, limited
meaning.

## Decision

Delivery order: a **single-node** cluster works end to end first (real GCP, P6 step 1), then a
**three-node HA** cluster (P6 step 2).

| Aspect | MVP behaviour |
|---|---|
| Node count | 1 to 30. HA requires at least 3. |
| Roles | Nodes 1–3 are master-eligible (`master, data, ingest`); later nodes are `data, ingest`. The master-eligible set never changes. |
| Cluster formation | `cluster.initial_master_nodes` is fixed at creation and removed once the cluster has formed, so nodes added later always join, never bootstrap a new cluster. |
| Zones | HA spreads nodes round-robin over up to three zones of the region (the chosen zone first) and enables shard allocation awareness on the zone attribute. Without HA every node is in the chosen zone. |
| Network | Private IPs only; 9200 and 9300 open only inside the cluster subnet ([ADR 0006](0006-dedicated-vpc-per-cluster.md)). |
| Security | TLS on HTTP and transport with a per-cluster CA, authentication and authorization enabled, no anonymous access. The built-in `elastic` user's password is in the customer's Secret Manager. |
| Storage | One Persistent Disk per node for data, separate from the boot disk, not auto-deleted with the VM. |
| JVM | Heap = half of the VM's memory, at most 31 GB; `vm.max_map_count` set; swap off. |
| Health | Single-node yellow (replicas unassigned) is expected and reported as healthy with a note ([ADR 0008](0008-separate-lifecycle-and-health.md)). |
| Failures | Detected and reported (node down, agent down, Elasticsearch down, disk and heap pressure). No automatic replacement, restart or failover by the platform. |
| Out of scope | Automatic node replacement, dedicated master nodes, cross-region replication, snapshots and restore, rolling upgrades, scale-down. |

## Consequences

- "HA" in the MVP means "survives the loss of one zone or node without data loss, if indices
  have replicas", not "heals itself". The console and documentation say so.
- Three master-eligible data nodes keep a quorum after one failure; losing two of the first three
  nodes makes the cluster UNHEALTHY until an operator restores them.
