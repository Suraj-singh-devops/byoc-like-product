"""Simulated GCP for MOCK_MODE: behaves like GCP + Terraform without touching either.

* Renders the real Terraform workspace (main.tf.json + modules) for inspection, but never runs
  Terraform.
* Deterministic triggers:
  - project ID containing ``denied``, ``disabled`` or ``notfound`` fails validation;
  - network or subnet names containing ``notfound``, ``othervpc``, ``proxy``, ``nonat``,
    ``nopga`` or ``small`` shape the network lookup.

  See docs/mock-mode.md.
"""

from __future__ import annotations

import zlib
from typing import Any

from app.domain.errors import CloudProviderError, PlatformError
from app.infrastructure.logging import get_logger
from app.providers.cloud.base import (
    CloudAccountContext,
    CredentialValidationResult,
    InfrastructureRequest,
    NetworkDetails,
    NetworkLookup,
    NodePlacement,
    ProvisionedNode,
    ValidationCheck,
)
from app.providers.cloud.gcp.errors import api_disabled, permission_denied
from app.providers.cloud.gcp.network import gcp_network_details
from app.providers.cloud.gcp.permissions import REQUIRED_APIS, REQUIRED_PERMISSIONS
from app.providers.cloud.gcp.terraform import (
    AgentArtifact,
    backend_config,
    module_variables,
    prepare_workspace,
    render_root_module,
)
from app.providers.cloud.mock.base import SimulatedCloudProvider, uuid_instance_id

log = get_logger(__name__)

# Resources every cluster gets. Addresses match the Terraform modules (a test checks them).
CLUSTER_RESOURCES = (
    ("module.cluster.module.iam.google_service_account.node", "Creating least-privilege node service account"),
    ("module.cluster.tls_self_signed_cert.ca", "Generating cluster CA and node TLS certificates"),
    (
        'module.cluster.google_secret_manager_secret.this["elastic-password"]',
        "Storing elastic password and TLS material in Secret Manager",
    ),
    ("module.cluster.module.artifacts.google_storage_bucket.this", "Creating private artifacts bucket"),
    ("module.cluster.module.artifacts.google_storage_bucket_object.agent[0]", "Uploading BYOC agent binary"),
)
# A cluster in a registered network (docs/adr/0013): only firewall rules scoped to its VMs.
NETWORK_RESOURCES = (
    (
        "module.cluster.module.network.google_compute_firewall.internal",
        "Creating firewall rule in {vpc}: allow 9200/9300 from {cidrs} to this cluster's VMs only",
    ),
)
# A cluster created before registered networks has its own VPC (docs/adr/0006).
DEDICATED_VPC_RESOURCES = (
    ("module.cluster.module.network.google_compute_network.this[0]", "Creating VPC network {prefix}-vpc"),
    (
        "module.cluster.module.network.google_compute_subnetwork.this[0]",
        "Creating subnet {prefix}-subnet (10.10.0.0/24, Private Google Access on)",
    ),
    ("module.cluster.module.network.google_compute_router.this[0]", "Creating Cloud Router {prefix}-router"),
    (
        "module.cluster.module.network.google_compute_router_nat.this[0]",
        "Creating Cloud NAT (outbound only; VMs get no public IPs)",
    ),
    (
        "module.cluster.module.network.google_compute_firewall.internal",
        "Creating firewall rule: allow 9200/9300 from the cluster subnet only",
    ),
)
NODE_RESOURCES = (
    'module.cluster.module.storage.google_compute_disk.data["{node}"]',
    'module.cluster.module.compute.google_compute_instance.node["{node}"]',
)


class MockGcpProvider(SimulatedCloudProvider):
    node_resources = NODE_RESOURCES

    # ---------------------------------------------------------------- accounts

    def validate_credentials(self, account: CloudAccountContext) -> CredentialValidationResult:
        self._pause(0.8)
        project = account.project_id
        checks: list[ValidationCheck] = []
        errors: list[PlatformError] = []

        principal = account.service_account_email or "unknown"
        checks.append(
            ValidationCheck("credentials", "Impersonation", "passed", f"Impersonating {principal} (simulated)")
        )

        if "notfound" in project:
            err = CloudProviderError(
                f"Project {project} was not found or the service account cannot access it.",
                code="GCP_PROJECT_NOT_FOUND",
                reason="resourcemanager.projects.get returned 403/404 for this project.",
                suggested_action="Check the project ID and grant the service account access to the project.",
            )
            checks.append(ValidationCheck("project", "Project access", "failed", str(err)))
            return CredentialValidationResult(valid=False, checks=checks, error=err.to_dict())
        checks.append(ValidationCheck("project", "Project access", "passed", f"Project {project} is active"))

        for service, label in REQUIRED_APIS.items():
            if "disabled" in project and service == "compute.googleapis.com":
                err = api_disabled(service, project)
                errors.append(err)
                checks.append(ValidationCheck(f"api:{service}", label, "failed", str(err)))
            else:
                checks.append(ValidationCheck(f"api:{service}", label, "passed", "Enabled"))

        missing: list[str] = []
        if "denied" in project:
            missing = ["compute.instances.create", "compute.disks.create", "iam.serviceAccounts.actAs"]
            err = permission_denied(missing[0], project, principal)
            err.details["missing_permissions"] = missing
            errors.append(err)
            checks.append(ValidationCheck("permissions", "IAM permissions", "failed", str(err)))
        else:
            checks.append(
                ValidationCheck(
                    "permissions", "IAM permissions", "passed", f"All {len(REQUIRED_PERMISSIONS)} permissions granted"
                )
            )
        return CredentialValidationResult(
            valid=not errors,
            checks=checks,
            missing_permissions=missing,
            error=errors[0].to_dict() if errors else None,
        )

    # ---------------------------------------------------------------- networks

    def describe_network(self, account: CloudAccountContext, lookup: NetworkLookup) -> NetworkDetails:
        """Synthesizes the Compute API responses a real lookup would get, deterministically."""
        self._pause(0.6)
        project, region = account.project_id, lookup.region
        vpc, name = lookup.vpc, lookup.subnets[0]
        base = f"https://www.googleapis.com/compute/v1/projects/{project}"
        network = (
            None
            if "notfound" in vpc
            else {
                "name": vpc,
                "selfLink": f"{base}/global/networks/{vpc}",
                "autoCreateSubnetworks": vpc == "default",
            }
        )
        subnet: dict[str, Any] | None = None
        if "notfound" not in name:
            digest = zlib.crc32(f"{project}/{region}/{name}".encode())
            second = 16 + digest % 200
            cidr = (
                f"10.{second}.{(digest >> 8) % 256}.{(digest >> 16) % 16 * 16}/28"
                if "small" in name
                else f"10.{second}.{(digest >> 8) % 16 * 16}.0/20"
            )
            subnet = {
                "name": name,
                "network": f"{base}/global/networks/{'shared-vpc' if 'othervpc' in name else vpc}",
                "ipCidrRange": cidr,
                "region": f"{base}/regions/{region}",
                "privateIpGoogleAccess": "nopga" not in name,
                "purpose": "REGIONAL_MANAGED_PROXY" if "proxy" in name else "PRIVATE",
                "stackType": "IPV4_ONLY",
                "selfLink": f"{base}/regions/{region}/subnetworks/{name}",
            }
        routers = (
            []
            if "nonat" in vpc or "nonat" in name
            else [
                {
                    "name": f"{vpc}-router",
                    "network": f"{base}/global/networks/{vpc}",
                    "nats": [{"name": f"{vpc}-nat", "sourceSubnetworkIpRangesToNat": "ALL_SUBNETWORKS_ALL_IP_RANGES"}],
                }
            ]
        )
        return gcp_network_details(
            project=project,
            lookup=lookup,
            zones=self.descriptor.region_zones(region),
            network=network,
            subnet=subnet,
            routers=routers,
        )

    # --------------------------------------------------------------- terraform

    def render_workspace(self, account: CloudAccountContext, request: InfrastructureRequest) -> None:
        try:
            variables = module_variables(
                request,
                agent=AgentArtifact("/opt/byoc/agent/byoc-agent-linux-amd64", self.settings.agent_version),
                control_plane_url=self.settings.control_plane_public_url,
                agent_audience=self.settings.agent_identity_audience,
            )
            root = render_root_module(request, variables, backend_config(request))
            prepare_workspace(self.settings.workspaces_dir, self.settings.terraform_modules_dir, request, root)
        except (OSError, ValueError) as exc:
            log.warning("mock_workspace_not_written", error=str(exc))

    def foundation(self, request: InfrastructureRequest) -> tuple[tuple[str, str], ...]:
        network = request.network
        if network is None:
            resources = DEDICATED_VPC_RESOURCES
            values = {"prefix": request.resource_prefix}
        else:
            resources = NETWORK_RESOURCES
            values = {"vpc": network.vpc.rsplit("/", 1)[-1], "cidrs": ", ".join(network.cidrs)}
        return tuple((address, message.format(**values)) for address, message in resources + CLUSTER_RESOURCES)

    def teardown_messages(self, request: InfrastructureRequest) -> tuple[str, ...]:
        common = (
            "Destroying secrets, TLS material and the artifacts bucket",
            "Destroying node service account",
        )
        if request.network is None:
            return (*common, "Destroying firewall rules, Cloud NAT and router", "Destroying subnet and VPC network")
        return (*common, "Destroying the cluster's firewall rule (the VPC and subnet are the customer's and stay)")

    def node_messages(self, request: InfrastructureRequest, node: NodePlacement) -> tuple[str, str]:
        instance = request.instance_name(node.name)
        return (
            f"Creating disk {instance}-data ({request.storage_gb} GB {request.storage_type}, encrypted)",
            f"Creating VM {instance} ({request.machine_type}, {node.zone}, no public IP)",
        )

    def instance_id(self, project_id: str, zone: str, instance_name: str) -> str:
        return uuid_instance_id(project_id, zone, instance_name)

    def hostname(self, request: InfrastructureRequest, zone: str, instance_name: str, private_ip: str) -> str:
        return f"{instance_name}.{zone}.c.{request.project_id}.internal"

    def outputs(self, request: InfrastructureRequest, nodes: list[ProvisionedNode]) -> dict[str, Any]:
        prefix, project = request.resource_prefix, request.project_id
        if request.network is None:
            network = f"projects/{project}/global/networks/{prefix}-vpc"
            subnetwork = f"projects/{project}/regions/{request.region}/subnetworks/{prefix}-subnet"
        else:
            network, subnetwork = request.network.vpc, request.network.subnets[0].id
        return {
            "network": network,
            "subnetwork": subnetwork,
            "service_account_email": f"byoc-{request.cluster_id[:8]}@{project}.iam.gserviceaccount.com",
            "secrets": {
                "elastic_password": f"{prefix}-es-elastic-password",
                "ca_certificate": f"{prefix}-es-ca-cert",
            },
            "artifacts_bucket": f"{prefix}-art-mock",
            "http_endpoints": [f"https://{n.private_ip}:9200" for n in nodes],
        }
