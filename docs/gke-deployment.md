# Deploying the platform on GKE

The control plane runs on GKE Autopilot (implementation plan P3). Docker Compose stays for local
development only.

```text
Internet ─ Google external L7 load balancer (Gateway API, managed certificate)
             ├─ /api  → api            (2 replicas)
             └─ /     → frontend       (console, 2 replicas)
           cluster-manager · terraform-runner · monitoring-worker · redis (StatefulSet)
           each Deployment: its own Kubernetes ServiceAccount → its own Google service account
             └─ Cloud SQL Auth Proxy sidecar → Cloud SQL for PostgreSQL (private IP, IAM login)
           secrets: Secret Manager, mounted by the GKE Secret Manager add-on
```

## What Terraform creates (`infrastructure/terraform/platform`)

| Area | Resources | Protections |
|---|---|---|
| Network | VPC, node subnet with Pod and Service ranges, Cloud Router + NAT, Private Service Access range | Nodes have no public IPs |
| GKE | Autopilot cluster, Workload Identity, Gateway API, Secret Manager add-on | Private nodes; control plane reachable only from `master_authorized_cidrs` (never `0.0.0.0/0`); Kubernetes secrets encrypted with KMS; dedicated node service account |
| Cloud SQL | PostgreSQL 17, database `byoc`, one IAM user per workload | No public IP, TLS only, IAM authentication (no passwords), CMEK, backups and PITR, deletion protection |
| Identities | `byoc-api`, `-manager`, `-runner`, `-monitor`, `-migrator`, `-redis` | Each bound only to its Kubernetes ServiceAccount; no keys; secret access per workload |
| Secrets | `byoc-secret-key`, `byoc-redis-password` (containers only) | Values added by `deploy/scripts/create-secrets.sh`, never in state |
| Images | Artifact Registry repository | Immutable tags; nodes may read |
| State | Bucket for customer Terraform state (P5) | Private, versioned, CMEK; only the runner may use it |
| Console | Global static IP; with `domain`, a Google-managed certificate (Certificate Manager) | HTTPS with HTTP redirect |

## Steps

```bash
# 1. Platform infrastructure (a platform project, never a customer project)
cd infrastructure/terraform/platform
cat > terraform.tfvars <<'VARS'
project_id              = "<platform-project>"
region                  = "asia-south1"
master_authorized_cidrs = ["<your office or CI egress IP>/32"]
domain                  = "byoc.example.com"   # optional
VARS
terraform init && terraform apply
terraform output dns_records     # create these records if a domain is set

# 2. Secret values (generated locally, piped to Secret Manager)
../../../deploy/scripts/create-secrets.sh <platform-project>

# 3. Images and the Helm release (uses its own kubeconfig; your kubectl context is unchanged)
cd ../../.. && deploy/scripts/deploy-gke.sh
```

The Helm values come from `terraform output -raw helm_values`. For an acceptance environment add
`--set settings.seedDemoData=true --set settings.demoPassword=<password>` to seed the demo
organizations.

## The Helm chart (`deploy/helm/database-platform`)

- One Deployment per role (api, cluster-manager, terraform-runner, monitoring-worker), the console
  and Redis, each with its own ServiceAccount; only the terraform-runner and the
  monitoring-worker build cloud providers (docs/adr/0004).
- A migration Job runs before every install and upgrade (Helm hook). It owns the schema, then
  grants the other workloads data access (`app/infrastructure/db_grants.py`).
- Restricted Pod Security: non-root, read-only root filesystem, no privilege escalation, all
  capabilities dropped, RuntimeDefault seccomp. The namespace enforces `restricted`.
- NetworkPolicies: default deny; the console reaches only the API; the API and the cluster-manager
  reach Redis, Cloud SQL and (for the proxy) Google APIs; only the terraform-runner and the
  monitoring-worker reach other hosts on 443; Redis accepts only backend pods.
- Two modes: `database.mode=cloudsql` + `secrets.source=secretManager` on GKE, or a database URL
  and Kubernetes Secrets on any Kubernetes (used by the local kind test).

## Tests

```bash
make test-platform   # Terraform validate + plan tests with mocked providers
make test-helm       # lint, render (GKE and plain Kubernetes), schema validation incl. Gateway and CSI CRDs
```

The chart was also installed on a local kind cluster (restricted Pod Security enforced, network
policies enforced by kindnet): the e2e acceptance test passed 41/41 through the console, the
policies allowed and blocked exactly the intended paths, and an upgrade re-ran the migration Job.
