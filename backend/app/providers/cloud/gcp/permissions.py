"""IAM permissions the control plane needs in the customer's project.

Keep this list in sync with the custom role in
infrastructure/terraform/gcp/modules/control-plane-access (a unit test checks they match).
"""

from __future__ import annotations

from typing import Any

REQUIRED_PERMISSIONS: tuple[str, ...] = (
    # Networking. Clusters run in the customer's registered network (docs/adr/0013): the platform
    # adds firewall rules scoped to the cluster's VMs and attaches VMs to the subnet, and never
    # creates or deletes networks, subnets, routers or NAT.
    "compute.networks.updatePolicy",
    "compute.networks.use",
    "compute.subnetworks.use",
    "compute.firewalls.create",
    "compute.firewalls.delete",
    "compute.firewalls.get",
    "compute.firewalls.update",
    # Network lookup (read-only); moves to the read-only monitor role in P4.
    "compute.networks.get",
    "compute.subnetworks.get",
    "compute.routers.list",
    # Compute and storage
    "compute.instances.create",
    "compute.instances.delete",
    "compute.instances.get",
    "compute.instances.list",
    "compute.instances.setMetadata",
    "compute.instances.setServiceAccount",
    "compute.instances.setTags",
    "compute.instances.setLabels",
    "compute.instances.attachDisk",
    "compute.instances.detachDisk",
    "compute.instances.start",
    "compute.instances.stop",
    "compute.instances.getGuestAttributes",
    "compute.disks.create",
    "compute.disks.delete",
    "compute.disks.get",
    "compute.disks.use",
    "compute.disks.setLabels",
    "compute.disks.resize",
    # Internal load balancer of the dedicated layout (docs/adr/0016).
    "compute.addresses.create",
    "compute.addresses.createInternal",
    "compute.addresses.delete",
    "compute.addresses.deleteInternal",
    "compute.addresses.get",
    "compute.addresses.setLabels",
    "compute.addresses.use",
    "compute.addresses.useInternal",
    "compute.instanceGroups.create",
    "compute.instanceGroups.delete",
    "compute.instanceGroups.get",
    "compute.instanceGroups.update",
    "compute.instanceGroups.use",
    "compute.instances.use",
    "compute.regionHealthChecks.create",
    "compute.regionHealthChecks.delete",
    "compute.regionHealthChecks.get",
    "compute.regionHealthChecks.update",
    "compute.regionHealthChecks.useReadOnly",
    "compute.regionBackendServices.create",
    "compute.regionBackendServices.delete",
    "compute.regionBackendServices.get",
    "compute.regionBackendServices.update",
    "compute.regionBackendServices.use",
    "compute.forwardingRules.create",
    "compute.forwardingRules.delete",
    "compute.forwardingRules.get",
    "compute.forwardingRules.setLabels",
    "compute.forwardingRules.update",
    "compute.forwardingRules.use",
    "compute.machineTypes.get",
    "compute.machineTypes.list",
    "compute.zones.get",
    "compute.zones.list",
    "compute.regions.get",
    "compute.regions.list",
    "compute.projects.get",
    "compute.zoneOperations.get",
    "compute.regionOperations.get",
    "compute.globalOperations.get",
    # Node service account
    "iam.serviceAccounts.create",
    "iam.serviceAccounts.delete",
    "iam.serviceAccounts.get",
    "iam.serviceAccounts.update",
    "iam.serviceAccounts.actAs",
    # Cluster secrets (TLS material, elastic password)
    "secretmanager.secrets.create",
    "secretmanager.secrets.delete",
    "secretmanager.secrets.get",
    "secretmanager.secrets.update",
    "secretmanager.secrets.getIamPolicy",
    "secretmanager.secrets.setIamPolicy",
    "secretmanager.versions.add",
    "secretmanager.versions.get",
    "secretmanager.versions.access",
    "secretmanager.versions.destroy",
    # Artifact bucket (agent binary)
    "storage.buckets.create",
    "storage.buckets.delete",
    "storage.buckets.get",
    "storage.buckets.update",
    "storage.buckets.getIamPolicy",
    "storage.buckets.setIamPolicy",
    "storage.objects.create",
    "storage.objects.delete",
    "storage.objects.get",
    "storage.objects.list",
    "resourcemanager.projects.get",
)

REQUIRED_APIS: dict[str, str] = {
    "compute.googleapis.com": "Compute Engine API",
    "iam.googleapis.com": "Identity and Access Management (IAM) API",
    "secretmanager.googleapis.com": "Secret Manager API",
    "storage.googleapis.com": "Cloud Storage API",
    "cloudresourcemanager.googleapis.com": "Cloud Resource Manager API",
}

_ROLE_HINTS: tuple[tuple[str, str], ...] = (
    ("compute.networks.", "roles/compute.networkAdmin"),
    ("compute.subnetworks.", "roles/compute.networkAdmin"),
    ("compute.routers.", "roles/compute.networkAdmin"),
    ("compute.firewalls.", "roles/compute.securityAdmin"),
    ("compute.addresses.", "roles/compute.networkAdmin"),
    ("compute.forwardingRules.", "roles/compute.loadBalancerAdmin"),
    ("compute.regionBackendServices.", "roles/compute.loadBalancerAdmin"),
    ("compute.regionHealthChecks.", "roles/compute.loadBalancerAdmin"),
    ("compute.", "roles/compute.instanceAdmin.v1"),
    ("iam.serviceAccounts.actAs", "roles/iam.serviceAccountUser"),
    ("iam.", "roles/iam.serviceAccountAdmin"),
    ("secretmanager.", "roles/secretmanager.admin"),
    ("storage.", "roles/storage.admin"),
    ("resourcemanager.", "roles/browser"),
)

CUSTOM_ROLE_HINT = (
    "or apply infrastructure/terraform/gcp/modules/control-plane-access, which creates a "
    "least-privilege custom role with exactly the permissions the platform needs"
)


def role_for_permission(permission: str) -> str:
    for prefix, role in _ROLE_HINTS:
        if permission.startswith(prefix):
            return role
    return "roles/editor"


def suggested_roles(permissions: list[str]) -> list[str]:
    return sorted({role_for_permission(p) for p in permissions})


def gcp_onboarding(organization_key: str) -> dict[str, Any]:
    """The grant a customer makes (docs/adr/0003). The identity project is assigned in P4."""
    return {
        "principal": f"serviceAccount:org-{organization_key}-tf@<identity-project>.iam.gserviceaccount.com",
        "role_name": "db-platform-provisioner",
        "permissions": list(REQUIRED_PERMISSIONS),
    }
