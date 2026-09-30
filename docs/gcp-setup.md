# GCP setup and IAM requirements

To provision real clusters, the platform needs to act in the customer's GCP project with
exactly the permissions below, and the required APIs must be enabled. The customer keeps
ownership: they can inspect every resource, and revoking the grant stops the platform at once.

> **Status.** Real GCP mode is disabled until phase P4 (`MOCK_MODE=false` is refused at
> startup). Customer access will go through per-organization platform identities, so that one
> organization can never use another's project
> ([ADR 0003](adr/0003-customer-access-per-organization-identities.md)). Service-account keys
> are not accepted in any mode. The steps below describe the provisioner service account the
> customer creates; the exact principal to grant it to is shown in the console once P4 lands.

## 1. Onboard the project

### Option A: Terraform (recommended)

```hcl
module "platform_access" {
  source     = "github.com/<org>/byoc//infrastructure/terraform/gcp/modules/control-plane-access"
  project_id = "customer-prod"

  # The platform identity of your organization (shown in the console).
  platform_principal = "serviceAccount:org-<key>-tf@<identity-project>.iam.gserviceaccount.com"
}
```

It enables the APIs, creates the custom role, the provisioner service account and the
impersonation grant. P4 extends it with a read-only monitor service account.

### Option B: gcloud

```bash
PROJECT=customer-prod
gcloud services enable compute.googleapis.com iam.googleapis.com secretmanager.googleapis.com \
  storage.googleapis.com cloudresourcemanager.googleapis.com --project $PROJECT

gcloud iam roles create byocControlPlane --project $PROJECT --title "Database platform provisioner" \
  --permissions "$(grep -o '"[a-z]*\.[A-Za-z]*\.[A-Za-z]*"' \
      infrastructure/terraform/gcp/modules/control-plane-access/permissions.tf | tr -d '"' | paste -sd, -)"

gcloud iam service-accounts create db-platform-provisioner --project $PROJECT
gcloud projects add-iam-policy-binding $PROJECT \
  --member "serviceAccount:db-platform-provisioner@$PROJECT.iam.gserviceaccount.com" \
  --role "projects/$PROJECT/roles/byocControlPlane"

# Allow your organization's platform identity to impersonate it. No key is ever created.
gcloud iam service-accounts add-iam-policy-binding db-platform-provisioner@$PROJECT.iam.gserviceaccount.com \
  --member "serviceAccount:<your organization's platform identity>" --role roles/iam.serviceAccountTokenCreator
```

Organizations that restrict IAM members to their own domain (domain restricted sharing) must
allow the platform's identity project before this grant can be made.

## 2. Add the cloud account

**Cloud accounts → Add cloud account → Google Cloud**: a name, the project ID, a default region and the email of
the service account to impersonate. The service account must belong to the project
(`…@<project-id>.iam.gserviceaccount.com`).

Validation runs these checks and reports each one. The account becomes `CONNECTED` when all
pass, `FAILED` when it never connected, and `DISCONNECTED` when a previously connected account
stops passing (for example after the grant is revoked). Creating, scaling or retrying needs a
`CONNECTED` account; deleting a cluster does not.

| Check | How |
|---|---|
| Impersonation | Mint a short-lived token for the service account (proves the customer's consent) |
| Project access | `projects.get`; the project must be ACTIVE |
| APIs enabled | One read call per API; a disabled API is reported with the `gcloud services enable` command |
| IAM permissions | `testIamPermissions` with every permission below; missing ones are listed with a suggested role |

Example of a failed validation:

```text
GCP authorization failed.
Reason: Service account does not have compute.instances.create permission.
Suggested action: Grant roles/compute.instanceAdmin.v1 to db-platform-provisioner@customer-prod.iam.gserviceaccount.com
on project customer-prod, or apply infrastructure/terraform/gcp/modules/control-plane-access, ...
```

Accounts created before keys were removed show `KEY_AUTH_REMOVED`: their stored key was
deleted by the upgrade. Grant impersonation on the same service account and validate again.

## 3. Control-plane settings

| Variable | Why |
|---|---|
| `MOCK_MODE` | `true` (the only accepted value until P4) |
| `SECRET_KEY` | ≥32 random characters in production; signs sessions and derives the local Terraform state key |
| `ELASTICSEARCH_VERSION` | Optional exact default version from the version catalog |
| `CONTROL_PLANE_PUBLIC_URL` | Optional HTTPS URL reachable from the VMs; enables agent heartbeats and approved actions |
| `COOKIE_SECURE=true` | When the console is served over HTTPS |

On GKE (P3 and later) the platform's own Google identity comes from Workload Identity; no key
file is mounted anywhere.

## Required permissions

The single source of truth is
[`control-plane-access/permissions.tf`](../infrastructure/terraform/gcp/modules/control-plane-access/permissions.tf)
(a backend test keeps it identical to the validator's list):

| Area | Permissions | Used for |
|---|---|---|
| Network | `compute.networks.updatePolicy`, `compute.networks.use`, `compute.subnetworks.use`, `compute.firewalls.*` | Firewall rules for the cluster's VMs in your registered network, VMs attached to your subnet. No network, subnet, router or NAT is created |
| Network lookup | `compute.networks.get`, `compute.subnetworks.get`, `compute.routers.list` | Read-only checks of the registered network; move to the read-only monitor role in P4 |
| Compute | `compute.instances.*` (create, delete, get, list, setMetadata, setServiceAccount, setTags, setLabels, attach/detachDisk, start, stop, getGuestAttributes) | VMs and reading their status/guest attributes |
| Storage | `compute.disks.*` (create, delete, get, use, setLabels, resize) | Data disks |
| Load balancer | `compute.addresses.*` (internal), `compute.instanceGroups.*`, `compute.instances.use`, `compute.regionHealthChecks.*`, `compute.regionBackendServices.*`, `compute.forwardingRules.*` | The dedicated layout's internal load balancer (one private IP on 9200) |
| Catalog | `compute.machineTypes/zones/regions.get,list`, `compute.projects.get`, `compute.*Operations.get` | Validation and Terraform polling |
| IAM | `iam.serviceAccounts.create/delete/get/update/actAs` | Node service account and attaching it to VMs |
| Secrets | `secretmanager.secrets.*`, `secretmanager.versions.add/get/access/destroy` | CA, node certificate/key, elastic password; per-secret access for the node SA |
| Buckets | `storage.buckets.*` (create, delete, get, update, get/setIamPolicy), `storage.objects.create/delete/get/list` | Private artifacts bucket holding the agent |
| Project | `resourcemanager.projects.get` | Validation |

The node VMs run as a per-cluster service account **with no project roles**: it can read only
its cluster's secrets and artifacts bucket.

## Networking notes

- Clusters run in a network you register in an environment: an existing VPC network and one
  subnet of this project ([ADR 0013](adr/0013-environments-and-registered-networks.md)). The
  platform looks it up (subnet range, purpose, Cloud NAT, Private Google Access) and never
  creates or changes it. Shared VPC is not supported yet.
- The subnet needs outbound HTTPS to `artifacts.elastic.co` (Cloud NAT or a proxy) and Private
  Google Access for Secret Manager and Cloud Storage; missing ones are shown as warnings.
- VMs have no external IPs. Clients reach the private endpoints (`https://<node-ip>:9200`, or
  the internal load balancer's address with the dedicated layout) from the registered subnet; other ranges need firewall rules you add (client ranges per network are
  planned).
- Clusters created before P1b keep their dedicated VPC ([ADR 0006](adr/0006-dedicated-vpc-per-cluster.md)).
- The platform itself never connects to the VMs or to Elasticsearch; it uses Google APIs only.

## What gets created per cluster

One or two firewall rules in your registered network (scoped to the cluster's network tag), a node
service account, four Secret Manager secrets, a private GCS bucket with the agent binary, one data
disk per node, one VM per node (with the dedicated layout: an internal address, instance groups,
a health check, a backend service, a forwarding rule and a health-check firewall rule), running exactly the catalog version of Elasticsearch
(9.5.4). Everything is labelled `managed-by=byoc` and `byoc-cluster-id=<id>`, and removed by
**Delete**.
