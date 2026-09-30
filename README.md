# BYOC Database Platform

A managed-database experience where the database infrastructure runs in the **customer's own
cloud account** (a GCP project or an AWS account), inside a network the customer already runs. The platform (control plane) provisions, monitors, scales and deletes the
database; the VMs, disks, network and data (data plane) stay in the customer's project, billed
by Google to the customer.

The MVP is **Elasticsearch on GCP Compute Engine**, with a control plane that runs on **GKE**.
AWS is supported in mock mode; real AWS follows in its own track
([ADR 0014](docs/adr/0014-aws-support.md)).
Work follows PRD v2 / TRD v2, the [architecture decision records](docs/adr/README.md) and the
[implementation plan](docs/implementation-plan.md). Phases **P1** (the prototype brought in line
with v2), **P1b** (environments, registered networks, GCP and AWS) and **P2** (privilege-separated
processes) are complete; GKE
deployment is P3 and real customer provisioning starts in P4–P6.

```text
 Web UI (Next.js) ──> API (FastAPI, /api/v1) ──> PostgreSQL (desired/actual state, operations, audit)
                          │
                          └─> Redis queue ──> Worker ──> CloudProvider (GCP) ──> Terraform/OpenTofu
                                                │                                   │
                                                └─> DatabaseProvider (Elasticsearch)│
                                                                                    ▼
                  Customer GCP project / AWS account: your VPC + subnet (registered network) · firewall · IAM
                                    Compute Engine VMs + Persistent Disks · Secret Manager
                                    Elasticsearch 9.5.4 + agent (outbound only)
```

## Quick start (local development, mock mode)

Docker Compose is for local development only; the deployment target is GKE (phase P3).

Requirements: Docker with Compose v2.

```bash
docker compose up --build        # first build takes a few minutes
open http://localhost:3000
```

Sign in with a demo account (password `demo-password`, set by `DEMO_PASSWORD`):

| Account | Role | Organization |
|---|---|---|
| `owner@acme.example` | Owner | Acme Corp |
| `admin@acme.example` | Admin | Acme Corp |
| `operator@acme.example` | Operator (create, scale, health-check; no delete) | Acme Corp |
| `viewer@acme.example` | Viewer | Acme Corp |
| `owner@globex.example` | Owner | Globex (a second tenant, for isolation) |

Then follow the flow: **Cloud accounts → Add cloud account** (keyless: a GCP service account to
impersonate, or an AWS role to assume with your organization's external ID) → **Create cluster**:
choose or create an **environment** (test or production), the **cloud provider**, and a
**network** (register an existing VPC and subnet; the platform fetches its details) → configure
and create → watch the operation → cluster details → **Scale up** → **Delete** (type the
cluster name) → **Audit logs**.

In mock mode, GCP, AWS, Terraform and the VMs are simulated realistically: operations move through
every state, nodes boot and report metrics, and **Simulate failure** injects VM, agent,
Elasticsearch, disk and heap faults that the monitor has to detect. No cloud resources are
created. See [docs/mock-mode.md](docs/mock-mode.md).

The API is documented at http://localhost:8000/api/docs (also proxied at
http://localhost:3000/api/docs).

Run the acceptance flow end to end against the running stack:

```bash
python3 tests/e2e/acceptance_test.py
```

## Real GCP provisioning

Disabled for now: `MOCK_MODE=false` is refused at startup. Customer projects will be reached
through per-organization platform identities that each customer grants explicitly, so that one
organization can never use another's project
([ADR 0003](docs/adr/0003-customer-access-per-organization-identities.md)). That arrives in
phase P4; service-account keys are not accepted in any mode. The Terraform modules, the
bootstrap script (exact version, signing-key fingerprint and SHA-256 checks) and the GCP
provider are in place and tested offline. See [docs/gcp-setup.md](docs/gcp-setup.md).

## Repository layout

| Path | What |
|---|---|
| [`backend/`](backend) | Control plane: FastAPI API (`app/api/v1`), cluster-manager, terraform-runner and monitoring-worker processes, domain model, providers, SQLAlchemy models, Alembic migrations, tests |
| [`frontend/`](frontend) | Web console: Next.js 16, React 19, TypeScript |
| [`agent/elasticsearch-agent/`](agent/elasticsearch-agent) | Go node agent (standard library only) |
| [`infrastructure/terraform/gcp/`](infrastructure/terraform/gcp) | Terraform/OpenTofu modules (network, iam, storage, compute, artifacts, elasticsearch, control-plane-access) and the prototype environment |
| [`infrastructure/terraform/platform/`](infrastructure/terraform/platform) | The platform on GKE: VPC, GKE Autopilot, Cloud SQL, identities, secrets, registry, KMS |
| [`deploy/`](deploy) | Helm chart `database-platform` and deploy scripts ([docs/gke-deployment.md](docs/gke-deployment.md)) |
| [`tests/e2e/`](tests/e2e) | Acceptance test against a running stack |
| [`docs/`](docs) | Architecture, ADRs, migration and implementation plans, setup, API, Terraform, agent, troubleshooting |

## Tests

```bash
make test-backend      # unit, API, provider, integration and migration tests (SQLite)
make test-agent        # Go tests with the race detector (runs in Docker)
make test-terraform    # terraform validate + plan tests with mocked providers
make test-frontend     # type-check and production build (runs in Docker)
make smoke             # acceptance flow against the running stack
```

The backend suite also runs against PostgreSQL: `TEST_DATABASE_URL=postgresql+psycopg://... make test-backend-postgres`.

## Documentation

- [Requirements](docs/requirements/README.md): PRD v1, PRD v2 and TRD v2
- [Architecture](docs/architecture.md): components, lifecycle and health, operations, security
- [Architecture decision records](docs/adr/README.md): the decisions behind v2
- [Migration plan](docs/migration-plan.md): v1 prototype → v2, what was kept, changed and removed
- [Implementation plan](docs/implementation-plan.md): phases P0–P9, dependencies, exit criteria
- [Local development](docs/local-development.md): running and testing each component
- [GKE deployment](docs/gke-deployment.md): platform Terraform, Helm chart, deploy steps
- [Mock mode](docs/mock-mode.md): what is simulated, fault injection, running the real agent locally
- [GCP setup and IAM](docs/gcp-setup.md): onboarding a customer project
- [API](docs/api.md): endpoints, permissions, errors, idempotency, audit events
- [Terraform](docs/terraform.md): modules, generated workspaces, state, plan safety
- [Agent](docs/agent.md): responsibilities, protocol, configuration
- [Troubleshooting](docs/troubleshooting.md)

## Scope of the MVP

In scope (PRD v2 and P1b): authentication, organizations and RBAC (Owner, Admin, Operator,
Viewer), keyless GCP and AWS cloud accounts, environments, registered networks, cluster creation, scale-up, confirmed deletion, health and metrics,
failure detection, operations, audit logs, mock mode, the Go agent and Terraform modules, a GKE
deployment.

Not in the MVP: scale-down, automatic recovery, backups and restore, rolling upgrades, other
engines, Azure, real AWS (its own track), SSO, billing. Elasticsearch licensing for a commercial offering is under
review ([ADR 0002](docs/adr/0002-elasticsearch-distribution-and-licensing.md)).
