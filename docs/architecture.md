# Architecture

The platform splits into a **control plane** that we operate and a **data plane** that runs in
the customer's GCP project. The control plane never holds customer data or credentials: it
holds desired state, operation history, audit logs and metric summaries, and reaches customer
projects only by impersonating a service account the customer controls.

This page describes the system as implemented today (phase P1 of the
[implementation plan](implementation-plan.md)). The target design is recorded in the
[architecture decision records](adr/README.md): the control plane runs on GKE as four
privilege-separated components ([ADR 0004](adr/0004-privilege-separated-components.md)), and
customer access goes through per-organization identities
([ADR 0003](adr/0003-customer-access-per-organization-identities.md)). Until that identity model
exists, real GCP mode is disabled and everything below runs in mock mode.

```text
                ┌──────────────────────────── control plane ────────────────────────────┐
 Browser ──────>│ Next.js console ──/api──> FastAPI (/api/v1) ─────> PostgreSQL          │
                │                              │  (desired/actual state, operations,     │
                │                              │   audit, events, metric samples)        │
                │                              └─> Redis (queues and wake-ups, locks)    │
                │                                        │                                │
                │ cluster-manager: workflows · reaper      no cloud access; cloud work is  │
                │   └─ DatabaseProvider (Elasticsearch)    a task (PostgreSQL + Redis):    │
                │ terraform-runner: validation, preflight, plan/apply/destroy  (writes)   │
                │ monitoring-worker: health monitor, node refresh, network lookup (reads) │
                │   └─ CloudProvider (GCP, AWS or simulated): only these two build one   │
                └───────────────┬──────────────────────────────────────▲─────────────────┘
                   Terraform +  │ Compute API                          │ heartbeat (HTTPS, optional)
                   Google APIs  ▼                                      │ guest attributes (always)
                ┌──────────────── customer GCP project (data plane) ───┴─────────────────┐
                │ cluster VPC + subnet · Cloud Router/NAT · firewall · node service acct │
                │ Secret Manager (CA, node cert/key, elastic password) · artifacts bucket │
                │ VMs (Shielded, no public IP) + Persistent Disks ── Elasticsearch 9.5.4  │
                │                                                  └─ byoc-agent          │
                └─────────────────────────────────────────────────────────────────────────┘
```

Docker Compose runs this locally for development. It is not the deployment target; the GKE
deployment (Helm chart, Cloud SQL, Workload Identity) is phase P3.

## Processes and privilege separation

| Process | Role setting | Cloud access | Does |
|---|---|---|---|
| API (`backend`) | `api` | none | Requests, auth, RBAC, audit; submits validation and lookups as tasks |
| cluster-manager | `cluster-manager` | none | Runs operation workflows and the reaper; reads node state only from PostgreSQL |
| terraform-runner | `terraform-runner` | write | Account validation, preflight, Terraform plan/apply/destroy |
| monitoring-worker | `monitoring-worker` | read | Health monitor, node refreshes, network lookups |

The registry builds cloud providers only for the two worker roles; elsewhere `registry.cloud()`
raises and every process uses the static descriptors (catalog, identifier checks, VM identity
verification with public keys). A task is a `cloud_tasks` row (payload, status, progress,
heartbeat, result or error) plus a Redis wake-up per role; workers also scan PostgreSQL, so a lost
wake-up only delays a task, and a waiter fails a task whose worker stopped heartbeating
(`TASK_ABANDONED`). Tasks of an operation stop at safe points when it is cancelled. In
real-GCP development mode only the two worker containers receive credentials
([ADR 0015](adr/0015-development-local-credentials.md)).

## Backend layout

| Package | Responsibility |
|---|---|
| `app/domain` | Pure model: lifecycle and health states with their transition tables (`states.py`), enums and audit event names, desired-state spec, RBAC, health model, report schema. No I/O. |
| `app/providers` | `CloudProvider` (GCP, Mock), `DatabaseProvider` (Elasticsearch, with its version catalog), `BackupProvider` (interface), and the registry that picks implementations. |
| `app/application` | Services (auth, organizations, cloud accounts, clusters, operations, health, agent) and the provisioning engine (runner, workflows, context, outcome). |
| `app/api` | `v1/` (routes and schemas of the public API), error envelope, request middleware, unversioned `/healthz`, `/readyz`, `/metrics`. |
| `app/models`, `app/repositories` | SQLAlchemy models and organization-scoped queries. |
| `app/infrastructure` | Database sessions, Redis queue, rate limiter, security (Argon2id, JWT, HKDF), Terraform runner, logging, metrics. |
| `app/workers` | `python -m app.workers.main <role>`: cluster-manager (operations, reaper), terraform-runner and monitoring-worker (cloud tasks; the health monitor runs in the monitoring-worker). |
| `app/application/tasks.py`, `task_handlers.py` | Cloud tasks: submit, wait, claim by compare-and-set, leases, progress and cancellation; the handlers run only where cloud providers exist. |

The core never branches on the cloud or the engine. Adding Redis means a
`RedisProvider(DatabaseProvider)` plus a Terraform stack; adding AWS means an
`AWSProvider(CloudProvider)`. Neither touches services, workflows, the API or the UI.

### Provider interfaces

Terraform is the only thing that writes customer infrastructure
([ADR 0005](adr/0005-terraform-only-writer-state-and-secrets.md)).

- `CloudProvider`: `validate_credentials`, catalog lookups, `validate_placement`,
  `plan_infrastructure` + `apply_infrastructure` (network, IAM, storage and compute as one
  Terraform plan), `destroy_infrastructure`, and read-only `get_resource_status`,
  `read_node_reports`, `verify_instance_identity`. There are no imperative create or delete
  calls.
- `DatabaseProvider`: `resolve_version` (exact catalog versions), `validate`, `provision`
  (topology), `configure` (settings and package pins rendered onto the VMs), `health`,
  `metrics`, `scale` (scale-up plans only), and `upgrade`/`backup`/`restore`/`delete` hooks
  that raise *not supported*. Providers render what gets installed; they never execute
  anything on the VMs.

## Desired state and operations

A cluster's **desired state** is a document stored with a `generation` counter; every change
(create, scale) increments it. Operations reconcile the actual state towards it and set
`observed_generation` when done, so the console shows *reconciling* versus *in sync*.

Every long-running action is an **operation** with an ID, a status (PENDING, VALIDATING,
PROVISIONING, BOOTSTRAPPING, CONFIGURING, HEALTH_CHECK, then COMPLETED, FAILED or CANCELLED;
states only move forward), a step timeline, a log and a structured error. The API records the
operation, enqueues its ID and returns `202`; HTTP requests never wait for infrastructure.

| Workflow | Steps |
|---|---|
| Create | validate request → validate GCP access → Terraform plan → apply → bootstrap (install) → configure/start → health check → register nodes |
| Scale up | validate → plan → apply (new VMs and disks) → bootstrap new nodes → join → cluster health |
| Delete | validate → Terraform destroy → remove registration |
| Health check | collect reports → evaluate → persist and record events |

Scale-down is not part of the MVP; the API rejects it with `SCALE_DOWN_NOT_SUPPORTED`
([ADR 0010](adr/0010-operation-semantics.md)).

### Reliability and concurrency

- **Claiming.** Workers claim an operation with a compare-and-set on its status
  (`PENDING → VALIDATING`), so duplicate queue messages are harmless. A mutating operation is
  not claimed while another mutation of the same cluster is still running.
- **Leases.** A running operation refreshes `heartbeat_at`. The reaper re-queues operations
  whose worker died (up to 3 attempts), cancels abandoned operations that were asked to stop,
  and re-enqueues pending operations whose queue message was lost. PostgreSQL is the source of
  truth; Redis is only a wake-up signal.
- **Idempotency.** Every step can be re-run from the start: Terraform converges on existing
  resources, and a retried scale-up keeps nodes a previous attempt created. `POST /clusters`
  and `POST /clusters/{id}/scale` accept an `Idempotency-Key`; a repeated delete returns the
  delete already in progress.
- **One change at a time.** Partial unique indexes allow at most one active mutation per
  cluster, plus one delete waiting behind it. Mutation requests lock the cluster row.
- **Delete pre-emption.** A confirmed delete cancels the cluster's queued operations at once
  and asks running ones to stop at their next safe point; the delete starts when they have
  stopped, and the operation that stopped wakes it. A confirmed delete cannot be cancelled.
- **Safe points.** Cancellation takes effect between steps and during waits, never in the
  middle of a Terraform apply.
- **Lifecycle ownership.** An operation only moves the cluster out of the state it put it in,
  so, for example, a scale finishing after a delete was requested leaves DELETING untouched.
- **Plan guard.** Any plan that would destroy or replace a VM or data disk is refused with
  nothing changed.

## Lifecycle and health

Lifecycle and health are stored separately
([ADR 0008](adr/0008-separate-lifecycle-and-health.md)).

| Resource | Lifecycle (set by operations) | Health (set by health evaluation) |
|---|---|---|
| Cluster | CREATING, ACTIVE, SCALING, UPGRADING (reserved), DELETING, FAILED, DELETED | UNKNOWN, HEALTHY, DEGRADED, UNHEALTHY, evaluated separately for infrastructure and Elasticsearch |
| Node | BOOTSTRAPPING, ACTIVE, DELETED | UNKNOWN, HEALTHY, UNHEALTHY, plus `instance_status` from the Compute API and `agent_status` (NOT_REPORTED, REPORTING, STALE) |

A failed or cancelled scale-up returns the cluster to ACTIVE with the desired state still
ahead of the actual state; the operation can be retried. The console derives one display
status: the lifecycle while an operation or failure is in progress, the health while ACTIVE.

## Elasticsearch topology and versions

- Nodes are `node-1..node-N`. The first three are master-eligible (`master,data,ingest`); more
  nodes are `data,ingest`. `cluster.initial_master_nodes` is fixed at creation and removed once
  the cluster forms, so nodes added later always join.
- High availability requires at least 3 nodes, spreads them round-robin over up to three zones
  and enables shard allocation awareness ([ADR 0011](adr/0011-elasticsearch-topology-and-ha.md)).
- Security is always on: a per-cluster CA, TLS on transport and HTTP, and the `elastic` password
  in Secret Manager in the customer's project.
- Versions come from the catalog `backend/app/providers/database/elasticsearch/versions.yaml`:
  exact versions only (9.5.4 today), each with its package repository, signing-key fingerprint,
  per-architecture SHA-256 and license-review status
  ([ADR 0002](adr/0002-elasticsearch-distribution-and-licensing.md)). The VM trusts the
  repository only after checking the key's fingerprint and installs the package only after
  checking its checksum.

## Node agent and reporting channels

The Go agent reports system and Elasticsearch metrics every 30 seconds. It communicates
outbound only and exposes no port ([ADR 0007](adr/0007-outbound-only-agent.md)).

1. **Guest attributes (always):** the agent and the bootstrap script write to GCE guest
   attributes, which the control plane reads through the Compute API. Monitoring and
   provisioning work even when the VMs cannot reach the control plane.
2. **Heartbeat (HTTPS, optional):** when `CONTROL_PLANE_PUBLIC_URL` is set, the agent registers
   with a Google-signed VM identity token (matched to the node's instance and zone), receives a
   per-node token (stored hashed), posts reports and receives approved actions.

The only approved action is `restart_engine` (compiled into the agent and repeated in the
control plane); nothing runs through a shell.

## Health and failure detection

The monitor evaluates every ACTIVE cluster every 15 seconds (one worker holds a Redis leader
lock); running operations evaluate the clusters they change.

- **Infrastructure:** VM missing or stopped (Compute API), disk ≥90% (unhealthy). A silent agent
  on a running VM makes the node UNKNOWN, not dead.
- **Elasticsearch:** node not responding, JVM heap ≥92% (unhealthy), cluster status red or not
  formed (unhealthy), yellow with more than one node, or nodes missing from the cluster
  (degraded). Yellow is expected on a single node.
- **Warnings** (disk ≥80%, CPU ≥90%, memory ≥95%, heap ≥85%) are shown but never change health.
- **Roll-up:** a minority of failing or silent nodes makes the cluster DEGRADED; a majority, red,
  or no master makes it UNHEALTHY.
- **Events:** `NODE_UNHEALTHY`, `NODE_STATUS_UNKNOWN`, `NODE_RECOVERED`, `CLUSTER_HEALTH_CHANGED`.
  Automatic replacement is a later phase.

## Multi-tenancy and RBAC

Every resource belongs to an organization, and every query filters by the caller's
organization, which always comes from the session. Another tenant's resources return **404**.
Membership and role are read on every request, so changes apply immediately
([ADR 0009](adr/0009-roles-and-permissions.md)).

| Role | Can |
|---|---|
| Owner | Everything, including managing Owners |
| Admin | Everything except managing Owners |
| Operator | Create, scale and health-check any cluster; cancel and retry those operations |
| Viewer | Read everything in the organization |

Only Owners and Admins delete clusters or manage cloud accounts, environments, networks and
members.

## Environments, clouds and networks

```text
Organization ─┬─ Cloud accounts   GCP project + SA to impersonate | AWS account + role (external ID)
              └─ Environments     TEST | PRODUCTION
                   └─ Networks    account + region + existing VPC + subnet(s), looked up in the cloud
                        └─ Clusters   zones and subnets chosen from the network
```

A network is a platform record of a VPC and subnets the customer already runs; the platform
never creates or changes them and only adds firewall rules (or a security group) scoped to the
cluster's VMs ([ADR 0013](adr/0013-environments-and-registered-networks.md)). GCP and AWS
implement the same `CloudProvider` interface; AWS is simulated until its own track
([ADR 0014](adr/0014-aws-support.md)). Clusters created before P1b have no network and keep
their dedicated VPC.

## Security controls

- **Customer access:** no credential material is stored. The platform impersonates a service
  account in the customer's project that must belong to that project; uploaded keys are
  rejected. Real GCP mode (`MOCK_MODE=false`) is refused at startup until per-organization
  identities make consent provable (P4).
- **Terraform:** runs with a minimal environment (the control plane's own secrets are not
  passed) and impersonation only, never key material. State and plans are encrypted
  client-side with OpenTofu; plan files are deleted after apply and a destroyed cluster's
  local state is removed. GCS state per organization and cluster with KMS, and secrets kept
  out of state, arrive in P5.
- **Network:** databases have no public endpoint and no external IPs; ingress is open only from
  the cluster subnet (plus optional client CIDRs); SSH only via IAP when explicitly enabled; OS
  Login on and project SSH keys blocked.
- **Least privilege:** the node service account has no project roles, only access to its
  cluster's secrets and bucket. The customer's provisioner role lists exactly the permissions
  validation checks with `testIamPermissions`.
- **Authentication:** Argon2id passwords with constant-time handling of unknown users; JWTs
  signed with an HKDF-derived key in an httpOnly, SameSite=Strict cookie (or a Bearer header);
  rate-limited login.
- **Protections:** security headers from the API, CSP and frame denial from the UI, request IDs
  on errors and never a stack trace; the backend refuses to start in production with default
  secrets.
- **Audit:** every change is recorded with TRD event names: `CLUSTER_*_STARTED` when accepted,
  `CLUSTER_CREATED` / `CLUSTER_*_COMPLETED` on success, `OPERATION_FAILED` or
  `OPERATION_CANCELLED` otherwise, plus cloud-account, member, login and fault-injection
  events.

## Observability

- Structured JSON logs carry `request_id`, `operation_id` and `cluster_id` context, with secrets
  redacted.
- Prometheus metrics at `/metrics` (API) and `:9101` (worker): API request count, latency and
  error codes; operation count and duration; provisioning duration; worker failures; cloud
  API and Terraform failures; agent heartbeats and connected agents; clusters by health; queue
  depth.
