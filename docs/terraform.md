# Terraform / OpenTofu

All customer infrastructure is created by Terraform modules under
[`infrastructure/terraform/gcp`](../infrastructure/terraform/gcp); nothing else writes customer
infrastructure ([ADR 0005](adr/0005-terraform-only-writer-state-and-secrets.md)). The control plane runs
**OpenTofu 1.12** (bundled in the backend image); Terraform ≥ 1.6 works as well
(`TERRAFORM_BINARY=terraform`, without state encryption). Google provider `>= 6, < 9`
(validated with 8.4).

## Modules

| Module | Creates |
|---|---|
| `network` | New clusters: only firewall rules (engine ports from the registered subnet's range to the cluster's tag) in the customer's existing network, passed as resource paths with the range from the platform's lookup, so no data source reads the network ([ADR 0013](adr/0013-environments-and-registered-networks.md)). Clusters created before P1b: a dedicated VPC + subnet, Cloud Router + NAT. Optional client CIDRs (never `0.0.0.0/0`), optional IAP SSH. |
| `iam` | The node service account (no project roles by default) |
| `storage` | One Persistent Disk per node (`pd-balanced`/`pd-ssd`/`pd-standard`, optional CMEK) |
| `compute` | Shielded VMs, no external IP, data disk attached as `data`, guest attributes on, OS Login on, project SSH keys blocked |
| `artifacts` | Private bucket (public access prevention) holding the agent binary; read access for the node SA only |
| `elasticsearch` | The engine stack: composes the modules above, generates a CA, node certificate and `elastic` password into Secret Manager (per-secret access for the node SA) and ships the bootstrap script, which installs exactly `es_version` after verifying the signing key fingerprint and the package SHA-256 from `es_package` |
| `control-plane-access` | One-time customer onboarding: APIs, custom role, provisioner service account, impersonation grant |

`environments/prototype` reads the exact version and package pins from the same catalog the
control plane uses (`backend/app/providers/database/elasticsearch/versions.yaml`). A version
that is not in the catalog fails with an error naming it.

`environments/prototype` is a runnable root module for one cluster
(`cp terraform.tfvars.example terraform.tfvars && tofu init && tofu apply`).

## How the control plane runs it

For every cluster operation the GCP provider prepares a workspace
`WORKSPACES_DIR/<cluster-id>/`:

- `modules/`: a fresh copy of the modules shipped with the control plane version;
- `main.tf.json`: a generated root module that calls `modules/elasticsearch` with the
  cluster's desired state (nodes and zones, machine type, disks, engine settings with the
  exact version and package pins, labels, agent artifact) and configures the backend (a local,
  encrypted state file; phase P5 moves state to a GCS bucket in the platform's state project,
  one prefix per organization and cluster).

Then `init` → `plan -json` → plan guard → `apply -json <plan>` → `output -json`. Progress
events stream into the operation's step messages ("Applied 14/31 resources").

**Plan guard.** Before applying, the plan's `resource_changes` are checked: if a
`google_compute_instance` or `google_compute_disk` would be destroyed or replaced, the
operation fails with `UNSAFE_PLAN` and nothing is changed (only a confirmed delete removes
them, through `destroy`). Boot image drift is ignored in the module
(`ignore_changes`) so a new Debian image never replaces a data node.

**Credentials and secrets.** Terraform runs with a minimal environment: `PATH`, proxy settings,
`GOOGLE_IMPERSONATE_SERVICE_ACCOUNT` and `TF_ENCRYPTION`. No key material is ever passed, and
the control plane's own secrets are never passed. State and plan files
are encrypted client-side (AES-GCM, PBKDF2 key derived per cluster from `SECRET_KEY`).
Plans are deleted after apply, and a destroyed cluster's local workspace is removed.

## Scaling

Nodes are a map keyed by node name (`for_each`), so adding `node-4` never renumbers other
nodes. Only scale-up exists in the MVP; removing nodes returns with scale-down.

## Tests

```bash
cd infrastructure/terraform/gcp/modules/elasticsearch
terraform init -backend=false && terraform validate
terraform test          # plan-only, mocked google/random/tls providers, no credentials
```

The tests assert, among others: one VM per node, **no public IP on any VM**, ingress limited
to the cluster subnet by default, exactly four secret grants for the node SA, the ARM image
for `t2a-*` machines, the per-architecture package checksum, and rejection of `latest`, minor
lines such as `9.5` and unverifiable package sources; in an existing network, no VPC, subnet,
router or NAT is created and only the registered subnet's range reaches the database ports;
network names instead of resource paths and a `0.0.0.0/0` client range are rejected.
AWS modules come in the AWS track (A2); AWS is simulated until then. `terraform fmt -recursive -check`
is part of `make lint`.
