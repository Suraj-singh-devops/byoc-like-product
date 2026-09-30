# ADR 0008: Lifecycle and health are stored separately

- Status: Accepted (implemented in P1)
- Date: 2026-09-27
- Related: PRD v2 FR-6; TRD v2 §25, §34

## Context

PRD FR-6 lists cluster states that mix what the platform is doing (CREATING, SCALING, DELETING)
with how the cluster is doing (HEALTHY, DEGRADED). TRD §25 defines a separate health model and
requires distinguishing infrastructure health from Elasticsearch health: a running VM does not
mean Elasticsearch is healthy. A combined field loses information (a cluster being scaled can
also be degraded) and lets the monitor and the operations overwrite each other.

## Decision

### Cluster

| Field | Values | Written by |
|---|---|---|
| `lifecycle_state` | CREATING, ACTIVE, SCALING, UPGRADING (reserved), DELETING, FAILED, DELETED | Operations only, through the transition table in `app/domain/states.py` |
| `health` | UNKNOWN, HEALTHY, DEGRADED, UNHEALTHY | Health evaluation only |

Health is evaluated for two components and combined (worst wins, in the order
HEALTHY < UNKNOWN < DEGRADED < UNHEALTHY):

- **infrastructure**: VMs from the Compute API and system metrics from the agent;
- **engine**: Elasticsearch as reported by the agents (cluster status, formed or not, nodes
  joined, per-node availability and heap).

| Situation | Cluster health |
|---|---|
| No report received yet | UNKNOWN |
| Green, every node healthy | HEALTHY |
| Single-node cluster, yellow (replicas cannot be allocated) | HEALTHY, with a note |
| Yellow with more than one node, fewer nodes joined than expected, or a minority of nodes unhealthy or unknown | DEGRADED |
| An agent stopped reporting while its VM runs | DEGRADED (the node is UNKNOWN; the VM is not assumed dead) |
| Red, no elected master, Elasticsearch unreachable on every node, or a majority of nodes unhealthy | UNHEALTHY |

Warnings (disk above 80%, CPU above 90%, memory above 95%, JVM heap above 85%) are reported as
warnings and do not change the health state; disk above 90% or heap above 92% makes the node
UNHEALTHY.

### Node

| Field | Values |
|---|---|
| `lifecycle_state` | BOOTSTRAPPING, ACTIVE, DELETED |
| `health` | UNKNOWN, HEALTHY, UNHEALTHY (worst of its infrastructure and engine health) |
| `instance_status` | As reported by the Compute API (RUNNING, TERMINATED, ..., NOT_FOUND) |
| `agent_status` | NOT_REPORTED, REPORTING, STALE |

### Display

The API returns lifecycle and health as separate fields. The console derives one display label:
the lifecycle while an operation or failure is in progress (Creating, Scaling, Deleting, Failed,
Deleted), and the health while the cluster is ACTIVE (Healthy, Degraded, Unhealthy, or Active when
health is still unknown).

## Consequences

- The monitor evaluates ACTIVE clusters and never changes lifecycle. A running operation
  evaluates the cluster it is changing (for example while waiting for new nodes to join).
- A finishing operation cannot overwrite a newer lifecycle (for example a scale completing after
  a delete was requested): it may only leave the state it put the cluster in, and every change
  goes through the transition table.
- Stored v1 values are migrated by Alembic revision 0002.
