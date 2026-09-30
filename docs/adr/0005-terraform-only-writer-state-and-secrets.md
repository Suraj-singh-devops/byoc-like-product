# ADR 0005: Terraform is the only infrastructure writer; state and secret handling

- Status: Accepted. Interfaces already follow it; state and secret changes land in P5.
- Date: 2026-09-27
- Related: TRD v2 §7, §8, §17, §18, §19

## Context

TRD §7 sketches a `CloudProvider` with imperative methods (`create_network`,
`create_compute_instance`, `delete_disk`), while TRD §18 says Terraform manages infrastructure
declaratively. Two writers would drift apart and break idempotency. v1 keeps Terraform state
locally (encrypted with a key derived from the platform secret) or in a customer bucket, and the
state contains the cluster CA key, the node private key and the `elastic` password, because
Terraform generates them.

## Decision

### One writer

- Terraform (OpenTofu, pinned in the runner image) is the only mechanism that creates, changes
  or deletes customer resources. There are no imperative create or delete calls through cloud
  SDKs.
- `CloudProvider` is limited to: credential and project validation, discovery (regions, zones,
  machine types), read-only status (instances, guest attributes), instance-identity
  verification, and Terraform orchestration (`plan_infrastructure`, `apply_infrastructure`,
  `destroy_infrastructure`, all executed by the terraform-runner).
- `DatabaseProvider` renders bootstrap inputs (topology, settings, version and package pins). It
  does not execute anything on VMs. Runtime actions on nodes happen only through allowlisted agent
  actions ([ADR 0007](0007-outbound-only-agent.md)).
- Only reviewed modules baked into the image are used. The generated root module contains
  validated variables only. Every change is `plan`, then the plan guard (refuses to destroy or
  replace VMs and data disks), then `apply` of that saved plan.

### State

- Backend: a GCS bucket in the platform's state project, one object prefix per
  `org/<organization-id>/cluster/<cluster-id>`. Versioning, uniform bucket-level access, public
  access prevention and soft delete are on.
- Access: each organization's write identity can read and write only its own prefix (IAM
  condition on the object name) and use only its own Cloud KMS key. The launcher, API,
  cluster-manager and monitoring worker have no access.
- Encryption: CMEK on the bucket, plus OpenTofu client-side state and plan encryption with a Cloud
  KMS key provider per organization, replacing v1's key derived from `SECRET_KEY`.
- Locking: the GCS backend's lock object, plus the database rule of one active mutation per
  cluster ([ADR 0010](0010-operation-semantics.md)).
- Retention: state of a deleted cluster is kept (versioned) for 30 days, then removed.

### Secrets stay out of state

- Terraform creates only the Secret Manager *secrets* (containers) in the customer project and
  their IAM: the cluster's node service account may read them and add versions.
- The first master-eligible node generates the cluster CA, the node certificate and key and the
  `elastic` password at first boot and adds them as secret versions. Other nodes wait for the
  versions and read them. The values never pass through the control plane and never appear in
  Terraform state or plan files.
- Users read the `elastic` password from Secret Manager in their own project; the console shows
  the secret name, not the value.
- Credential rotation (PRD §12) will be an allowlisted agent action that writes a new secret
  version and updates Elasticsearch; the design is part of P7.

## Consequences

- TRD §7's imperative method names are intentionally not implemented; the reason is recorded here.
- The first-boot secret generation adds a start-up dependency between nodes (the others wait for
  node 1's secret versions); the bootstrap timeout covers it.
- v1's `tls_*` and `random_password` resources are removed from the module in P5, and the state
  and plan files are tested to contain no private key or password.
