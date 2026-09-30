# ADR 0003: Customer access through per-organization identities

- Status: Accepted. Implemented in P4 (identities, onboarding, validation) and P5 (runner jobs).
  Until then, real GCP mode is disabled (P1).
- Date: 2026-09-27
- Related: PRD v2 §12 and §18 (multi-tenancy risk); TRD v2 §5, §6, §26, §27, §43

## Context

The platform changes infrastructure in customers' GCP projects. v1 supported two ways in, both
unacceptable for v2:

1. **Uploaded service-account keys**, stored encrypted in the platform database and used inside
   the worker. TRD §43 forbids service-account keys in pods, and a database leak would expose
   every customer.
2. **Impersonation by one shared platform identity.** Every customer granted the same platform
   service account permission to impersonate a service account in their project. This is a
   confused deputy: organization A could register organization B's project and service account,
   and the platform, holding B's grant, would act on B's project on A's behalf. A single
   credential also reached every customer.

The requirement is that organization A must never be able to register or use organization B's
project, and that no single shared runner identity has access to every customer project.

## Decision

### Identity chain

```text
GKE Pod (Terraform job for one operation)
  Kubernetes ServiceAccount  database-platform-runs/tr-<org-key>       one per organization
      │ Workload Identity
      ▼
  Organization write identity  org-<org-key>-tf@<identity-project>      one per organization, no roles of its own
      │ impersonation (granted by the customer, on this service account only)
      ▼
  Customer provisioner SA      db-platform-provisioner@<customer-project>  custom role, least privilege
      ▼
  Customer resources in that project

monitoring-worker Pod
  Kubernetes ServiceAccount  ksa-monitoring-worker
      │ Workload Identity
      ▼
  gsa-monitoring-worker  ──(token creator on each org read identity)──▶  org-<org-key>-mon@<identity-project>
      │ impersonation (granted by the customer)
      ▼
  Customer monitor SA          db-platform-monitor@<customer-project>     read-only custom role
```

- `<org-key>` is the first 16 hex characters of SHA-256 of the organization's UUID. Identities
  are never reused: a new organization always gets new names.
- Each organization has two platform identities in a dedicated identity project: `-tf` (write,
  used only by Terraform jobs) and `-mon` (read, used only by the monitoring worker). The customer
  grants `roles/iam.serviceAccountTokenCreator` on its provisioner SA to `-tf` only, and on its
  monitor SA to `-mon` only. A compromised monitoring worker therefore cannot write.
- Terraform runs as a Kubernetes Job per Terraform action, using the organization's own
  Kubernetes ServiceAccount (see [ADR 0004](0004-privilege-separated-components.md)). No
  long-lived process holds a credential that reaches more than one customer. The terraform-runner
  Deployment that launches the jobs has no customer-reachable permissions of its own.
- Read-only monitoring uses one shared monitoring identity that may impersonate each
  organization's read identity. Its reach is limited to what the customers' monitor role allows
  (instance status and guest attributes). Moving monitoring to per-organization Pods is possible
  later if that residual risk is not acceptable.
- The API, the cluster-manager and the frontend hold no customer access at all.

### How ownership (consent) is proven

The proof is the customer's own IAM policy. A cloud account becomes `CONNECTED` only when the
terraform-runner, running as that organization's write identity, can impersonate the registered
provisioner SA. Only the owner of the customer project can create that grant, and it names one
specific organization's identity.

- **Organization A cannot use B's project.** If A registers B's project, validation runs as A's
  identity, which B never trusted. Impersonation fails and the account stays `FAILED`; creating,
  scaling or retrying needs a `CONNECTED` account. Registering a project is only an unverified
  claim with no capability. (Deletion is allowed whatever the account's state, so a cluster is
  never stranded; it fails with a clear error if access was really revoked.)
- **The organization is never taken from the client.** The organization comes from the
  authenticated session; which identity to use is derived on the server from the cloud account
  row, which is scoped to that organization. The Terraform job re-reads its operation from the
  database and refuses to run if the operation's organization does not match the identity it
  runs as.
- **Validation checks** (all as the impersonated identities):
  1. the write identity can mint a token for the provisioner SA (consent);
  2. the provisioner SA's email belongs to the registered project
     (`…@<project-id>.iam.gserviceaccount.com`);
  3. the project exists and is active, and the required APIs are enabled;
  4. `testIamPermissions` on the project returns every required permission, and flags an
     over-privileged provisioner (for example one that can change the project's IAM policy);
  5. the monitor SA works and cannot write.
- **Errors do not leak.** A failed impersonation returns the same message whether the service
  account does not exist or does not trust the organization, so validation cannot be used to
  probe other customers' projects.
- **Revocation** is immediate: the customer removes the grant. The next validation or monitoring
  pass marks the account `DISCONNECTED`.
- **Offboarding** deletes the organization's identities; grants that still name them in
  customer projects become inert.

### Onboarding in the prototype

1. A platform operator creates the organization's identities with the
   `infrastructure/terraform/platform/tenant-identity` module (P4). The console shows the exact
   principals to grant.
2. The customer applies the `customer-onboarding` Terraform module (or the equivalent `gcloud`
   commands) in their project: provisioner SA with the custom role, monitor SA with a read-only
   role, required APIs, and the two token-creator grants.
3. The user registers the project, region and the two service-account emails, and runs
   validation.

## Alternatives rejected

- **Service-account keys**: forbidden by TRD §43, long-lived, and a database leak exposes every
  customer.
- **One shared platform identity plus a proof token** (for example a label the customer sets):
  proves who registered the project, but the shared identity still reaches every customer, so a
  compromised runner or a mistake in the check exposes everyone.
- **A shared runner identity that impersonates per-organization identities**: fixes the confused
  deputy, but one standing credential still reaches every customer. Rejected for write access;
  accepted only for read-only monitoring, as described above.

## Consequences

- Two service accounts per organization in the identity project. The default limit of 100 service
  accounts per project must be raised, or organizations sharded across identity projects.
- Customers whose organization policy restricts IAM members to their own domain (domain
  restricted sharing) must allow the platform's identity project's organization.
- The terraform-runner must orchestrate Kubernetes Jobs (P5), with an admission policy that pins
  the job image and entrypoint.
- In P1, uploaded keys are removed and `MOCK_MODE=false` is refused at startup, so real GCP
  cannot be used until this design is implemented in P4.
