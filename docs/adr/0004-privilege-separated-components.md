# ADR 0004: Four privilege-separated backend components

- Status: Accepted. Process split implemented in P2 (2026-09-28); Kubernetes identities in P3, runner jobs in P5.
- Date: 2026-09-27
- Related: PRD v2 §3, §12, §14; TRD v2 §4, §5, §19

## Context

The PRD lists seven control-plane components (frontend, API, cluster manager, provisioning
worker, operation worker, monitoring worker, Terraform runner) with overlapping duties. The TRD
asks for a dedicated Kubernetes ServiceAccount per workload and least-privilege IAM, and warns
against unnecessary microservices. v1 runs every background duty in one worker process with one
identity.

## Decision

The backend is one code base and one container image, run as four components that differ in
privilege. The frontend is separate.

| Component | Responsibility | Kubernetes SA | Google identity | Customer access | Platform access |
|---|---|---|---|---|---|
| frontend | Serves the console; proxies `/api` to the API | `ksa-frontend` | none | none | none |
| api | Authentication, RBAC, validation, desired state, creates operations, audit; never waits for infrastructure | `ksa-api` | `gsa-api` | **none** | Cloud SQL client, its own secrets |
| cluster-manager | Runs operation workflows (the PRD's provisioning and operation workers); decides what Terraform must do and waits for results | `ksa-cluster-manager` | `gsa-cluster-manager` | **none** | Cloud SQL client, Redis |
| terraform-runner | The only writer to customer infrastructure. Claims Terraform jobs and runs each one as a Kubernetes Job under the organization's own identity ([ADR 0003](0003-customer-access-per-organization-identities.md)); also runs cloud-account validation | `ksa-terraform-runner` (launcher); `tr-<org-key>` per organization (jobs) | `gsa-terraform-runner` with no customer-reachable permission; `org-<org-key>-tf` per organization | write, only inside a job for that organization | Cloud SQL client; create Jobs in the runs namespace; per-organization state prefix and KMS key (jobs) |
| monitoring-worker | Collects instance status and agent reports, evaluates health, records metrics and events; refreshes nodes for running operations | `ksa-monitoring-worker` | `gsa-monitoring-worker` | **read-only**, through each organization's read identity | Cloud SQL client, Redis |

Rules that follow from the table:

- Only the terraform-runner can construct write credentials for a customer project, and only
  inside a job whose organization matches the operation it re-reads from the database.
- The cluster-manager never calls cloud APIs. It sees the data plane only through the database,
  which the monitoring worker and agent heartbeats keep current. Bootstrap and health waits in
  workflows therefore read node rows, not the cloud.
- Cloud-account validation needs the write identity (it checks the provisioner's permissions), so
  it runs in the terraform-runner as an asynchronous operation; the account shows `VALIDATING`
  meanwhile.
- The API serves regions and machine types from a static catalog; the workflow verifies placement
  against the real project before planning.
- Components communicate only through PostgreSQL (source of truth) and Redis (queues, locks). The
  runner accepts nothing but an operation or job ID and derives everything else itself.
- Each component receives only the configuration it needs (for example the API never gets the
  Terraform settings).

Local development runs the same four roles as separate Docker Compose services. Compose has no
Workload Identity; mock mode needs none, and the terraform-runner executes jobs in-process there.

## Consequences

- Three identities per environment plus two per organization; no JSON keys anywhere.
- The terraform-runner is the highest-value target: it gets its own node pool or compute class,
  a NetworkPolicy that allows egress only to Google APIs and the database, no ingress, and an
  admission policy that pins the job image and entrypoint.
- The PRD's separate "provisioning worker" and "operation worker" are folded into the
  cluster-manager; running them as separate Deployments later is a configuration change.
