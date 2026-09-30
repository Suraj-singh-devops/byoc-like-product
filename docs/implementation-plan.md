# Implementation plan (v2)

Goal (PRD §19): GKE platform → customer GCP project → Elasticsearch cluster → monitor → scale →
delete, with the decisions in [docs/adr](adr/README.md). The starting point and the mapping from
v1 are in [migration-plan.md](migration-plan.md).

Rules for every phase:

- The application stays runnable and every test suite stays green.
- Mock mode stays the default for development and CI.
- A phase ends with a report (what changed, files, tests and results, security review, remaining
  issues, commands, readiness) and does not start the next phase automatically.
- No real customer project is touched before P4, and no real provisioning before P5.

## Phases

| Phase | Goal | Scope | Exit criteria |
|---|---|---|---|
| **P0 Decisions** | Lock the architecture | ADRs 0001–0012, migration plan, this plan | Recorded in `docs/` |
| **P1 Conform mock mode to v2** | The existing implementation speaks v2 and follows the new rules | v2 vocabulary; separate lifecycle and health (infrastructure vs engine); Operator role and per-action permissions; `app/api/v1`; exact-version catalog with checksums; operation semantics (delete confirmation, pre-emption, non-cancellable delete, scale-up only); TRD audit events; key upload removed and real mode disabled until P4; Alembic 0002 with data migration; frontend, e2e test and docs updated | Backend (SQLite and PostgreSQL), Go, Terraform and frontend type-check pass; e2e acceptance passes on the rebuilt Compose stack; migration tested on a copy of the running v1 database |
| **P1b Environments, networks, GCP and AWS (mock mode)** | Clusters are created in an environment, on a chosen cloud, inside a network the customer registered ([ADR 0013](adr/0013-environments-and-registered-networks.md), [ADR 0014](adr/0014-aws-support.md)) | Environments (TEST, PRODUCTION); registered networks (existing VPC and subnets, details fetched from the cloud, statuses and checks); cluster placement from the network (zones, subnets, HA over three zones); AWS cloud accounts (role ARN, per-organization external ID) and a simulated AWS provider; GCP Terraform in existing-network mode with explicit resource paths; provisioner permissions without network creation; RBAC and audit events; Alembic 0003 (default environment for existing clusters, external IDs); console flow environment → provider → network → cluster; e2e and docs | Same gates as P1; migration tested on a copy of the running P1 database |
| **P2 Privilege-separated processes** | The four backend roles exist as separate processes | `cluster-manager`, `terraform-runner`, `monitoring-worker` entrypoints; Terraform job protocol through PostgreSQL and Redis; cloud-account validation becomes an asynchronous runner operation; static region and machine-type catalog in the API; workflows read node state only from the database; per-role configuration; tests proving the API and cluster-manager never build cloud clients | Compose runs api, cluster-manager, terraform-runner, monitoring-worker and frontend; e2e passes; import and runtime guards pass |
| **P3 Platform on GKE (mock mode)** | The platform runs on GKE | `infrastructure/terraform/platform` (VPC, private GKE Autopilot cluster, Cloud SQL with private IP and IAM auth, Artifact Registry, per-role GSAs and Workload Identity bindings, state bucket, KMS); `deploy/helm/database-platform` (Deployments per role, KSAs, Gateway with managed certificate, NetworkPolicies default-deny, restricted Pod security, migration Job, in-cluster Redis, secrets from Secret Manager); image build and push | PRD success criteria 1–2 on GKE with `MOCK_MODE=true`; e2e passes against the GKE URL; no JSON keys anywhere |
| **P4 Customer onboarding** | A customer project connects with per-organization identities | `platform/tenant-identity` module (per-organization write and read GSAs, runner KSAs, Workload Identity bindings); `customer-onboarding` module (provisioner and monitor SAs, custom roles, APIs, token-creator grants); cloud-account API with provisioner and monitor SAs and generated grant instructions; validation through the impersonation chain; uniform errors; lift the real-mode gate | A sandbox customer project reaches CONNECTED; a second organization registering the same project stays FAILED (tested live) |
| **P5 Terraform runner hardening** | Real, isolated, safe Terraform | One Kubernetes Job per Terraform action under the organization's KSA; admission policy pinning image and entrypoint; GCS state per organization and cluster with per-organization IAM conditions and KMS; OpenTofu KMS encryption; provider mirror in the image; secrets generated on the first node, not in Terraform; separate firewall module; timeouts and cancellation; drift check before scale | `plan` from GKE against the sandbox project; state and plan verified free of secrets; a job cannot read another organization's state |
| **P6 First real cluster** | Elasticsearch runs in the customer project | Startup script with fingerprint and SHA-256 checks (done in P1) exercised for real; agent install; bootstrap and health gates; single node first, then 3-node HA across zones | PRD success criteria 5–9 on real GCP |
| **P7 Agent and monitoring v2** | Reliable health from real VMs | Decision on publishing the agent endpoint (Cloud Armor) or guest attributes only; short-lived agent credentials; disk IOPS, disk latency and JVM memory pressure metrics; credential rotation design; Operator restart action if wanted | VM down, agent down and Elasticsearch down each detected correctly on real VMs |
| **P8 Scale and delete for real** | Lifecycle complete on GCP | Scale-up 3 → 5; confirmed, pre-empting delete; kill the runner mid-apply and retry; verify no duplicate resources | PRD success criteria 10–15 |
| **P9 CI/CD and full end-to-end** | Repeatable delivery | Pipeline: lint, unit tests, image build, image and dependency scanning, secret scanning, Kubernetes manifest scanning, Helm package, Terraform fmt/validate/plan and scanning; nightly real-GCP e2e with cleanup; runbooks | Pipeline green; nightly e2e green |

After the MVP: scale-down (drain and voting exclusions, restoring the v1 implementation from the
snapshot), upgrades, backups and restore, automatic recovery, private connectivity,
per-organization monitoring identities, client CIDRs per network, Shared VPC, VPC and subnet
discovery, environment policies.

### AWS track

Real AWS runs as its own track once the GCP path has proven each pattern
([ADR 0014](adr/0014-aws-support.md)). Mock-mode AWS is part of P1b.

| Phase | Goal | Scope | Exit criteria |
|---|---|---|---|
| **A1 AWS access** | Keyless, per-organization access to a sandbox AWS account | Platform AWS account; per-organization roles trusted through Google web identity; external IDs; customer onboarding module (provisioner and monitor roles); real validation and network lookup | Sandbox account CONNECTED; a second organization with the same role ARN stays FAILED |
| **A2 AWS Terraform modules** | Reviewed modules for the AWS data plane | Security group, instance profile, EBS gp3, EC2 (IMDSv2, no public IP), Secrets Manager, S3 artifacts; `tofu test` with mocked providers | Modules validate and pass plan tests offline |
| **A3 First real AWS cluster** | Elasticsearch runs in a customer AWS account | Runner jobs as for GCP; bootstrap with the same checks; single node, then 3 nodes over 3 AZs | PRD success criteria 5–9 on AWS |
| **A4 AWS monitoring** | Reliable health from EC2 | Agent identity via instance identity document; report channel decision; status reads through the monitor role | VM, agent and Elasticsearch failures detected on real instances |

A1 needs P3 and P4 (Workload Identity and the per-organization identity pattern). A2 can start
right after P1b. A3 needs P5, A1 and A2. A4 needs A3.

## Dependencies

```text
P0 ─▶ P1 ─▶ P1b ─▶ P2 ─┬─▶ P3 ─┐
                       │       ├─▶ P4 ─▶ P5 ─▶ P6 ─▶ P8 ─▶ P9 (real e2e)
                       └───────┘
P7: mock-side work after P2; real-VM work after P6
CI (lint and tests) can start right after P1 and grows with each phase
AWS track: A2 after P1b; A1 after P4; A3 after P5, A1 and A2; A4 after A3
```

- P2 needs P1's domain model and P1b's environments and networks (network lookups move to the
  monitoring worker). P4 needs P2 (validation runs in the runner) and P3 (identities exist on
  GKE). P5 needs P4 (organization identities). P6 needs P5. P8 needs P6.
- The critical path is P1 → P1b → P2 → P3 → P4 → P5 → P6 → P8.

## Mocked and real, by phase

| Component | P1–P2 | P3 | P4 | P5 | P6+ |
|---|---|---|---|---|---|
| API, auth, RBAC, operations, audit, UI | Real | Real on GKE | Real | Real | Real |
| PostgreSQL, Redis | Real (containers) | Cloud SQL, Redis in GKE | same | same | same |
| Workload Identity for platform services | n/a (Compose) | Real | Real | Real | Real |
| Customer identities and validation | Simulated | Simulated | **Real** (sandbox project) | Real | Real |
| Terraform | Modules tested with mocked providers; apply simulated | same | same | **Real plan** | **Real apply** |
| VMs, Elasticsearch, agent | Simulated data plane; real agent against a local Elasticsearch container | same | same | same | **Real** |

## P1 checklist (complete, 2026-09-27)

- [x] ADRs 0001–0012, migration plan, this plan
- [x] `domain/states.py`: lifecycle, health, node, agent, cloud-account and operation states with transition tables
- [x] RBAC: Operator, per-action permissions, delete limited to Owner and Admin
- [x] Health: node UNKNOWN / HEALTHY / UNHEALTHY with warnings; cluster UNKNOWN / HEALTHY / DEGRADED / UNHEALTHY; infrastructure and engine components; agent status
- [x] Version catalog (`versions.yaml`) with exact versions, checksums, signing key and license review status; `ELASTICSEARCH_VERSION`; exact install in Terraform and the startup script
- [x] Operation semantics: delete confirmation, pre-emption, idempotent delete, non-cancellable delete, cancel and retry permissions by type, lifecycle ownership, successor wake-up, connected account required for create, scale and retry
- [x] Scale-up only; scale-down code removed (backend, mock data plane, agent allowlist)
- [x] Audit events per TRD §35
- [x] Cloud accounts: v2 states, region, key upload removed, real mode refused until P4
- [x] `app/api/v1` package
- [x] Alembic 0002 with data migration and tests; dry run on a copy of the v1 database
- [x] Frontend, seed data, e2e test, docs updated
- [x] All test suites green; e2e green on a fresh stack and on the upgraded local stack

## P3c checklist: HA topology and configuration (built and tested locally, 2026-09-30)

- [x] Dedicated layout ([ADR 0016](adr/0016-ha-topology-dedicated-roles.md)): 3 masters, data and coordinating groups with their own machine types and disks, forced zone awareness, role-aware health, scale by group, Alembic 0005
- [x] Internal load balancer in the GCP module (private address in the node certificate, health-check firewall on the load-balanced nodes only); provisioner permissions; simulated in mock mode
- [x] Configuration ([ADR 0017](adr/0017-configuration-management.md)): allowlisted settings, `GET`/`PUT /clusters/{id}/config`, `UPDATE_CONFIG` with live apply and rolling restart (elected master last), resumable retry, `cluster:configure` for Owner and Admin, audit events
- [x] Agent `configsync` (compiled-in allowlist, elected master applies, every node reports hash and generation) and `apply-config`; allowlists checked identical across backend, agent, Terraform and script
- [x] Console: topology choice and group editor in the wizard, groups and endpoint on the cluster page, configuration editor with review, scale by group
- [ ] Real run on the sandbox project (8 VMs + load balancer; needs approval: billable)

## P3 checklist (built and tested locally, 2026-09-29; live GKE rollout awaits approval)

- [x] `infrastructure/terraform/platform`: VPC, private GKE Autopilot, Cloud SQL (private IP, IAM login, CMEK), Artifact Registry, per-workload GSAs with Workload Identity, Secret Manager secret containers, KMS, state bucket, managed certificate; plan tests with mocked providers
- [x] `deploy/helm/database-platform`: Deployments per role, ServiceAccounts, Gateway + HTTPRoute + health checks, default-deny NetworkPolicies, restricted Pod Security, migration Job (hook), Redis StatefulSet, secrets from Secret Manager, Cloud SQL Auth Proxy sidecars
- [x] App support: secrets from files (`*_FILE`), Redis password, `RUN_MIGRATIONS`, database grants for IAM users
- [x] Image build and push, `deploy/scripts/deploy-gke.sh`, `deploy/scripts/create-secrets.sh`
- [x] Local Kubernetes (kind): install under restricted Pod Security, e2e 41/41, network policies verified, upgrade
- [ ] Apply the platform Terraform in a platform project and run the e2e against the GKE URL (needs approval: billable)

## P2 checklist (complete, 2026-09-28)

- [x] Roles: `SERVICE_ROLE` api, cluster-manager, terraform-runner, monitoring-worker (and `all` for local tools); `python -m app.workers.main <role>`
- [x] Registry builds cloud providers only for the terraform-runner and the monitoring-worker; static descriptors everywhere (catalog, identifier checks, onboarding, VM identity verification)
- [x] Cloud task protocol: `cloud_tasks` in PostgreSQL (Alembic 0004), Redis wake-ups per role, compare-and-set claims, heartbeats, abandonment, progress into the operation log, cancellation at safe points
- [x] Account validation and network validation asynchronous in the workers; network preview waits briefly for the monitoring-worker
- [x] Workflows (cluster-manager) read node state only from PostgreSQL; refreshes run in the monitoring-worker
- [x] Guards: runtime (`CloudAccessDenied`), import (API process loads no provider or credential code), tests
- [x] Compose runs api, cluster-manager, terraform-runner, monitoring-worker and frontend; real-GCP credentials only in the two workers; e2e passes

## P1b checklist (complete, 2026-09-27)

- [x] ADR 0013 (environments and registered networks; supersedes 0006 for new clusters) and ADR 0014 (AWS, mock first, keyless per-organization access)
- [x] Environments (TEST, PRODUCTION) with create, list, delete-when-empty
- [x] Registered networks: identifiers only from the user, details from a read-only cloud lookup, statuses and checks, preview lookup, re-validation, remove-when-unused
- [x] Cluster placement from the network: account and region from the network, zones and subnets, HA over three zones, subnet capacity, network re-check before create and scale (`NETWORK_CHANGED`)
- [x] AWS: cloud accounts with role ARN and per-organization external ID, simulated validation, network lookup and provisioning; onboarding endpoint with the trust policy
- [x] GCP Terraform existing-network mode with explicit resource paths; provisioner permissions without network creation; module tests
- [x] RBAC (`environment:*`, `network:*`) and audit events
- [x] Alembic 0003 with data migration and tests; dry run on a copy of the P1 database
- [x] Console: Environments pages, network registration, provider choice, create wizard
- [x] All suites green; e2e green on a fresh stack and on the upgraded local stack
