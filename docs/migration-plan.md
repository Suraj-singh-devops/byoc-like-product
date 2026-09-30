# Migration plan: v1 prototype to the v2 architecture

This plan maps the existing prototype (built from PRD v1) onto PRD v2 and TRD v2 and the
decisions recorded in [docs/adr](adr/README.md). It says what is reused, what is refactored,
what is removed and what is new, and in which phase. The phases themselves are described in
[implementation-plan.md](implementation-plan.md).

A snapshot of the tree before any v2 change is kept at
`.snapshots/v1-prototype-before-p1-2026-09-27.tar.gz` (the directory is not a git repository).
It contains the v1 scale-down implementation (drain and voting exclusions) that P1 removes and a
later phase will need.

## 1. Where we start (v1)

```text
Web UI (Next.js) ─> API (FastAPI, /api/v1) ─> PostgreSQL (desired/actual state, operations, audit)
                        │
                        └─> Redis queue ─> one "worker" process
                                             ├─ operation runner: create / scale up+down / delete / health check
                                             │    └─ CloudProvider (GCP or Mock) ─> OpenTofu in the same process
                                             ├─ health monitor (reads GCP / guest attributes directly)
                                             └─ reaper
Customer project: dedicated VPC, NAT, firewall, node SA, Secret Manager, VMs + disks, Elasticsearch, Go agent
```

v1 already provides: asynchronous operations with the PRD state names, Idempotency-Key, one active
mutation per cluster (enforced by a partial unique index), a Terraform plan guard, per-organization
tenancy (other organizations get 404), audit entries, an outbound-only agent with identity-token
registration and an allowlist, the guest-attribute reporting channel, a realistic mock mode,
Terraform modules with offline tests, and about 190 backend tests plus Go, Terraform and
end-to-end tests.

## 2. Where we are going (v2)

```text
Users ─> HTTPS load balancer ─> GKE (namespace database-platform)
          frontend ─> api ─────────────> Cloud SQL (PostgreSQL)   Redis (in GKE)
                       cluster-manager ─> runs operation workflows; no customer access
                       terraform-runner ─> only writer; one Kubernetes Job per Terraform run,
                                           as the organization's own identity
                       monitoring-worker ─> read-only customer access
Customer project ─ provisioner SA (trusts only its organization's platform identity)
                 ─ monitor SA (read-only)
                 ─ cluster VPC ─ subnet ─ Elasticsearch VMs + disks + agent (outbound only)
```

## 3. Component map

| Area | v1 today | v2 target | Action | Phase |
|---|---|---|---|---|
| API routes | `app/api/routes/*.py`, `app/api/schemas.py`, served under `/api/v1` | `app/api/v1/*.py` package with its own router and schemas (TRD §3) | Refactor (move) | P1 |
| Authentication | Argon2id, JWT in httpOnly cookie or Bearer, login rate limit | Unchanged | Reuse | — |
| Organizations and tenancy | Every query scoped by organization; other organizations get 404 | Unchanged | Reuse | — |
| RBAC | Owner, Admin, Developer (manages only clusters it created), Viewer | Owner, Admin, Operator, Viewer; only Owner and Admin delete ([ADR 0009](adr/0009-roles-and-permissions.md)) | Refactor | P1 |
| Cluster state | `status` (PROVISIONING, RUNNING, ...) and `health` (HEALTHY, WARNING, CRITICAL, UNKNOWN) | `lifecycle_state` (CREATING, ACTIVE, SCALING, UPGRADING, DELETING, FAILED, DELETED) and `health` (UNKNOWN, HEALTHY, DEGRADED, UNHEALTHY), infrastructure and Elasticsearch health reported separately ([ADR 0008](adr/0008-separate-lifecycle-and-health.md)) | Refactor | P1 |
| Node state | `status` mixes lifecycle and observations (UNREACHABLE, STOPPED, MISSING) | `lifecycle_state` (BOOTSTRAPPING, ACTIVE, DELETED), `health` (UNKNOWN, HEALTHY, UNHEALTHY), `instance_status`, `agent_status` | Refactor | P1 |
| Cloud accounts | Service-account key upload or impersonation of a customer SA; PENDING_VALIDATION, VALID, INVALID | Impersonation only; PENDING, VALIDATING, CONNECTED, FAILED, DISCONNECTED; `region`. Per-organization identities and verified consent in P4 ([ADR 0003](adr/0003-customer-access-per-organization-identities.md)) | Refactor, remove keys | P1 (states, keys), P4 (identities) |
| Operations | State machine, idempotency, leases, reaper, cancel, retry | Same, plus delete pre-emption, delete confirmation, non-cancellable delete, scale-up only ([ADR 0010](adr/0010-operation-semantics.md)) | Refactor | P1 |
| Workflows | Create, scale up, scale down (drain), delete, health check | Create, scale up, delete, health check | Remove scale-down | P1 |
| Audit | `action` + `status` ACCEPTED, then SUCCESS or FAILURE | TRD §35 event names (CLUSTER_SCALE_STARTED, CLUSTER_SCALE_COMPLETED, OPERATION_FAILED, ...) | Refactor | P1 |
| Engine versions | Minor lines 9.5, 9.4, 8.19; aliases `latest`, `9.x`; VMs install `elasticsearch=9.5.*` | YAML catalog of exact versions with package source, signing key, SHA-256 and license-review status; exact install ([ADR 0002](adr/0002-elasticsearch-distribution-and-licensing.md)) | Replace | P1 |
| Worker process | One process: runner, monitor, reaper | cluster-manager, terraform-runner, monitoring-worker ([ADR 0004](adr/0004-privilege-separated-components.md)) | Split | P2 |
| Cloud-account validation | Synchronous, inside the API request, using customer credentials | Asynchronous, executed by the terraform-runner (the API holds no customer access) | Move | P2 |
| Region and machine-type catalog | API calls GCP in real mode | Static catalog in the API; placement verified inside the workflow | Refactor | P2 |
| Terraform execution | In the worker; local state encrypted with a key derived from SECRET_KEY, or a customer bucket | Per-operation Kubernetes Job under the organization's identity; GCS state per organization and cluster with KMS; secrets kept out of state ([ADR 0005](adr/0005-terraform-only-writer-state-and-secrets.md)) | Refactor | P5 |
| Terraform modules | network (with firewall rules), iam, storage, compute, artifacts, elasticsearch, control-plane-access | Separate firewall module; existing-VPC input kept for later; customer onboarding module with provisioner and monitor SAs; exact version pin | Refactor | P1 (version pin), P4, P5 |
| Secrets in Terraform | CA, node key and elastic password generated by Terraform (in state) | Generated on the first node and written to the customer's Secret Manager; state holds only the empty secrets and IAM | Refactor | P5 |
| Agent | Outbound; guest attributes; allowlist drain_nodes, undrain_nodes, restart_engine | Allowlist restart_engine only ([ADR 0007](adr/0007-outbound-only-agent.md)) | Refactor | P1 |
| Mock mode | Full simulation including drain | Same without drain; the default for development and tests | Refactor | P1 |
| Frontend | v1 vocabulary; key-upload form | v2 vocabulary; actions driven by permissions; display status derived from lifecycle and health | Refactor | P1 |
| Docker Compose | Described as the quickstart | Optional local development only | Docs | P1 |
| Helm chart, GKE | None | `deploy/helm/database-platform` | New | P3 |
| Platform infrastructure | None | `infrastructure/terraform/platform` (GKE, Cloud SQL, Artifact Registry, GSAs, Workload Identity, state bucket, KMS) | New | P3 |
| CI/CD | Makefile targets | Pipeline with scanning (TRD §40–41) | New | starts after P1, finishes P9 |

## 4. Reused as they are

- Authentication, sessions, rate limiting, security headers, error envelope, request IDs, JSON logs
  with redaction, Prometheus metrics.
- Organization scoping in `app/repositories/queries.py`.
- Operation runner mechanics: compare-and-set claim, lease heartbeat, reaper, step timeline,
  cancellation at safe points, idempotency keys.
- Provider abstractions (`CloudProvider`, `DatabaseProvider`, `BackupProvider`) and the registry.
- Mock data plane (boot timeline, fault injection) minus drain.
- Elasticsearch topology rules (first three nodes master-eligible, `initial_master_nodes` frozen,
  round-robin zones, allocation awareness).
- Terraform modules for network, IAM, storage, compute and artifacts; the plan guard; the startup
  script structure; the Terraform test suite.
- Go agent: collectors, guest-attribute publisher, identity registration, heartbeat client.
- Frontend structure, components, charts and accessibility work.

## 5. Refactored in P1

| Item | Change |
|---|---|
| `domain/enums.py`, `domain/state_machine.py` | Lifecycle, health, node, agent, cloud-account and operation states move to `domain/states.py` with their transition tables. `enums.py` keeps roles, operation types and audit events. |
| `domain/rbac.py` | Operator replaces Developer; permissions per action (`cluster:scale`, `cluster:delete`, ...); the "own clusters only" rule is removed. |
| `domain/health.py`, Elasticsearch `provider.health` | Node health UNKNOWN / HEALTHY / UNHEALTHY with warnings kept as reasons; cluster health UNKNOWN / HEALTHY / DEGRADED / UNHEALTHY; separate `infrastructure` and `engine` components. A stale agent makes the node UNKNOWN and the cluster DEGRADED. |
| `models/*`, Alembic | `lifecycle_state` columns, `agent_status`, `cloud_accounts.region` and `last_connected_at`; credential columns dropped; operation uniqueness split so a delete can wait behind the operation it pre-empts. Revision 0002 migrates existing rows. |
| `application/cluster_service.py` | Scale-up only; delete requires `confirm=<cluster name>`, pre-empts other operations and is idempotent while in progress. Cluster rows are locked (`SELECT ... FOR UPDATE`) for every mutation request. |
| `application/operation_service.py`, `provisioning/*` | Delete operations cannot be cancelled; cancel and retry need the permission of the operation's type; the claim waits while another mutation is still running; finished operations wake the operation waiting behind them; lifecycle changes go through the transition table so a finishing scale cannot overwrite DELETING. |
| `application/audit_service.py` and callers | TRD event names. |
| Elasticsearch versions | `versions.yaml` catalog plus loader; `ELASTICSEARCH_VERSION` picks the default; exact versions only. |
| `providers/cloud/gcp`, Terraform `elasticsearch` module, `startup.sh` | Exact version, signing-key fingerprint and per-architecture SHA-256 flow from the catalog to the VM; `es_major_version` removed. |
| `api/*` | Moved into `api/v1`; responses expose `lifecycle`, `health`, `agent_status`; `can_manage` replaced by permissions from `/auth/me`. |
| Seed data, e2e test, frontend, docs | v2 vocabulary; `operator@acme.example` replaces `developer@acme.example` for new databases. |

## 6. Removed in P1

| Removed | Why |
|---|---|
| Service-account key upload (API field, parsing, `GOOGLE_CREDENTIALS` path, encrypted credential columns, `CREDENTIALS_ENCRYPTION_KEYS`, UI option) | TRD §43: no service-account keys in pods. Existing key-based accounts are marked FAILED or DISCONNECTED and their key material is dropped by the migration. |
| Real GCP mode until P4 (`MOCK_MODE=false` is refused at startup) | Until per-organization identities exist, the platform cannot prove that the organization registering a project is the one the customer trusts ([ADR 0003](adr/0003-customer-access-per-organization-identities.md)). |
| Scale-down: drain and undrain steps, voting exclusions, removable-node plumbing, DECOMMISSIONING, drain simulation, agent allowlist entries | Not in the MVP ([ADR 0010](adr/0010-operation-semantics.md)); the API now rejects it explicitly. |
| Version aliases (`latest`, `9`, `9.x`, `8.x`), minor-line pins, mock patch table | TRD §9 and §38: exact, deliberate versions only. |
| Developer role and cluster ownership checks | Replaced by Operator ([ADR 0009](adr/0009-roles-and-permissions.md)). |
| Audit status ACCEPTED and verb-style actions | Replaced by event names. |
| `app/api/routes/`, `app/api/schemas.py` | Replaced by `app/api/v1/`. |

## 7. Data migration (Alembic revision 0002)

| Table | v1 value | v2 value |
|---|---|---|
| `clusters.status` renamed `lifecycle_state` | PROVISIONING, RUNNING, SCALING, DELETING, DELETED, FAILED | CREATING, ACTIVE, SCALING, DELETING, DELETED, FAILED |
| `clusters.health` | HEALTHY, WARNING, CRITICAL, UNKNOWN | HEALTHY, DEGRADED, UNHEALTHY, UNKNOWN |
| `clusters.engine_version`, `desired_state.engine.version` | 9.5, 9.4, 8.19 | 9.5.4, 9.4.7, 8.19.22 (the patches the prototype ran). Only 9.5.4 is in the catalog, so clusters on other versions cannot be scaled until upgrades exist. |
| `cluster_nodes.status` renamed `lifecycle_state` | PROVISIONING, BOOTSTRAPPING / RUNNING, UNREACHABLE, STOPPED, MISSING, DECOMMISSIONING / DELETED | BOOTSTRAPPING / ACTIVE / DELETED |
| `cluster_nodes.health` | HEALTHY / WARNING, UNKNOWN / CRITICAL | HEALTHY / UNKNOWN / UNHEALTHY (recomputed on the next monitor pass) |
| `cluster_nodes.agent_status` (new) | — | NOT_REPORTED (recomputed on the next monitor pass) |
| `cloud_accounts.status` | PENDING_VALIDATION, VALID, INVALID | PENDING, CONNECTED, FAILED |
| key-based cloud accounts | VALID or INVALID with a stored key | DISCONNECTED or FAILED, key dropped, validation result explains how to reconnect |
| `organization_members.role` | DEVELOPER | OPERATOR |
| `audit_logs.action` and `status` | e.g. SCALE_CLUSTER + ACCEPTED, SCALE_CLUSTER + SUCCESS | CLUSTER_SCALE_STARTED, CLUSTER_SCALE_COMPLETED (full table in the migration) |
| active scale-down operations | PENDING or running with `to` < `from` | FAILED with SCALE_DOWN_NOT_SUPPORTED; the cluster returns to ACTIVE |
| `operations.metadata.params.previous_status` | v1 lifecycle names | `previous_lifecycle` with v2 names |

The downgrade reverses the schema and maps states back where a v1 equivalent exists (UPGRADING,
VALIDATING and DISCONNECTED have none and map to the nearest v1 state). Key material cannot be
restored.

## 8. Breaking API changes in P1

| Before | After |
|---|---|
| `status` on clusters and nodes | `lifecycle` and `health` (and `agent_status`, `instance_status` on nodes) |
| `can_manage` on cluster detail | Removed; clients use `permissions` from `GET /api/v1/auth/me` |
| `DELETE /clusters/{id}` | Requires `?confirm=<cluster name>`; returns the in-progress delete operation if one exists |
| `POST /clusters/{id}/scale` with fewer nodes | 422 `SCALE_DOWN_NOT_SUPPORTED` |
| `version` accepts `9.x`, `latest`, `9.5` | Exact catalog versions only (`9.5.4`); omitted means the catalog default |
| `POST /cloud-accounts` with `service_account_key` | 422; `service_account_email` (impersonation) and `region` required |
| Audit `action` values | Event names (see [api.md](api.md)) |
| Role `DEVELOPER` | `OPERATOR` |

## 9. Risks of the migration itself

- Vocabulary changes touch every layer. Mitigation: one Alembic revision with a data-migration
  test on SQLite and PostgreSQL, a dry run on a copy of the running v1 database, and the
  end-to-end acceptance test against the rebuilt stack.
- Removing the key path leaves no way to use real GCP until P4. This is intended; real
  provisioning is out of scope until then.
- The frontend and backend must be deployed together (breaking API changes). Compose builds
  both; the Helm chart in P3 versions them together.

## 10. P1b: environments, registered networks, GCP and AWS (Alembic revision 0003)

Requested on 2026-09-27 after P1: clusters are created in an environment, on a chosen cloud,
inside an existing network the customer registers
([ADR 0013](adr/0013-environments-and-registered-networks.md),
[ADR 0014](adr/0014-aws-support.md)). ADR 0006 (dedicated VPC per cluster) is superseded for new
clusters.

| Area | Change |
|---|---|
| Domain | `Environment` (TEST, PRODUCTION), `Network` (status PENDING, VALIDATING, AVAILABLE, FAILED, UNAVAILABLE); `domain/network.py` zone planning (HA = three zones); cluster spec carries environment, network and zones |
| Providers | `check_account`, `check_network_lookup`, `describe_network`, `onboarding` on the interface; GCP lookup through the Compute API (read-only); simulated GCP and AWS providers on one simulated data plane; AWS catalog, identifier checks, network lookup, draft permissions |
| Terraform | Existing-network mode takes resource paths and the subnet range from the platform, no data sources; validations reject names and `0.0.0.0/0`; provisioner permissions no longer create networks, subnets, routers or NAT |
| API | `/environments`, `/environments/{id}/networks`, `/networks/{id}`, `/networks/lookup`, `/cloud-providers`, `/cloud-accounts/onboarding`; cluster create takes `environment_id` and `network_id` |
| RBAC and audit | `environment:read/manage`, `network:read/manage` (Operator and Viewer read only); ENVIRONMENT_CREATED/DELETED, NETWORK_CREATED/VALIDATED/DELETED |
| Console | Environments pages, network registration with fetched details, provider choice on cloud accounts (AWS external ID and trust policy), create wizard environment → provider → network → configure |

Data migration (0003):

| Table | Change |
|---|---|
| `organizations.external_id` (new) | `byoc-` + 32 random hex characters per organization, unique |
| `environments` (new) | One `default` (PRODUCTION) per organization that has clusters |
| `clusters.environment_id` (new) | Every existing cluster, including deleted ones, in its organization's `default`; `desired_state.environment` added |
| `clusters.network_id` (new) | NULL for existing clusters: they keep their dedicated VPC |
| `cloud_accounts.role_arn` (new), `networks` (new) | Empty |

The downgrade refuses while clusters that are not deleted run in registered networks (0002 would
treat them as dedicated-VPC clusters); otherwise it removes the new tables and columns and marks
AWS accounts FAILED.

Breaking API changes:

| Before | After |
|---|---|
| `POST /clusters` with `cloud_account_id`, `region`, `zone` | `environment_id` and `network_id` required; account and region come from the network; `zone` optional |
| `POST /cloud-accounts` with `provider: "gcp"` only | `provider` `gcp` or `aws`; `role_arn` for AWS |
| `DELETE /cloud-accounts/{id}` | Also 409 while registered networks use the account |
