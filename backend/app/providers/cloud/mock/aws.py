"""Simulated AWS for MOCK_MODE (docs/adr/0014). No AWS SDK, credential or Terraform is involved.

Deterministic triggers (see docs/mock-mode.md):

* role ARN containing ``untrusted``: the role cannot be assumed (trust policy or external ID);
* role ARN containing ``denied``: the role lacks required actions;
* VPC or subnet ID containing ``dead``: not found; subnet ID containing ``0bad``: in another VPC;
  ``beef``: no route to a NAT gateway; ``cafe``: assigns public IPs on launch;
* a subnet's availability zone is chosen by the last hex digit of its ID (0 → a, 1 → b, 2 → c,
  3 → a again in a region with three zones).
"""

from __future__ import annotations

import zlib
from typing import Any

from app.domain.errors import CloudProviderError
from app.providers.cloud.aws.network import aws_network_details
from app.providers.cloud.aws.permissions import PROVISIONER_ACTIONS
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
from app.providers.cloud.mock.base import SimulatedCloudProvider, stable_digest

FOUNDATION = (
    (
        "module.cluster.aws_security_group.nodes",
        "Creating security group {prefix}-nodes in {vpc}: allow 9200/9300 from {cidrs} only",
    ),
    ("module.cluster.aws_iam_role.node", "Creating least-privilege instance role and profile"),
    ("module.cluster.tls_self_signed_cert.ca", "Generating cluster CA and node TLS certificates"),
    (
        'module.cluster.aws_secretsmanager_secret.this["elastic-password"]',
        "Storing elastic password and TLS material in Secrets Manager",
    ),
    ("module.cluster.aws_s3_bucket.artifacts", "Creating private artifacts bucket"),
    ("module.cluster.aws_s3_object.agent[0]", "Uploading BYOC agent binary"),
)
NODE_RESOURCES = (
    'module.cluster.aws_ebs_volume.data["{node}"]',
    'module.cluster.aws_instance.node["{node}"]',
)
MISSING_ACTIONS = ["ec2:RunInstances", "ec2:CreateVolume", "iam:PassRole"]


class MockAwsProvider(SimulatedCloudProvider):
    node_resources = NODE_RESOURCES

    # ---------------------------------------------------------------- accounts

    def validate_credentials(self, account: CloudAccountContext) -> CredentialValidationResult:
        self._pause(0.8)
        role = account.role_arn or ""
        checks: list[ValidationCheck] = []
        if "untrusted" in role or not account.external_id:
            # The same message whether the role is missing or does not trust this organization, so
            # validation cannot be used to probe other customers' accounts.
            err = CloudProviderError(
                f"The platform could not assume role {role}.",
                code="AWS_ASSUME_ROLE_FAILED",
                reason=(
                    "The role does not exist, or its trust policy does not allow this organization's platform "
                    "role with this organization's external ID."
                ),
                suggested_action=(
                    "Set the role's trust policy to the principal and external ID shown under Cloud accounts, "
                    "then validate again."
                ),
            )
            checks.append(ValidationCheck("credentials", "Assume role", "failed", str(err)))
            return CredentialValidationResult(valid=False, checks=checks, error=err.to_dict())
        checks.append(
            ValidationCheck(
                "credentials",
                "Assume role",
                "passed",
                f"Assumed {role} with the organization's external ID (simulated)",
            )
        )
        checks.append(
            ValidationCheck(
                "account", "Account", "passed", f"sts:GetCallerIdentity returned account {account.project_id}"
            )
        )
        region = account.region or ""
        if region in {r.name for r in self.descriptor.regions}:
            checks.append(ValidationCheck("region", "Region", "passed", f"{region} is enabled"))
        if "denied" in role:
            err = CloudProviderError(
                f"Role {role} is missing actions the platform needs.",
                code="AWS_PERMISSION_DENIED",
                reason=f"Not allowed: {', '.join(MISSING_ACTIONS)}.",
                suggested_action=(
                    f"Attach the platform's policy (the {len(PROVISIONER_ACTIONS)} actions listed under Cloud "
                    "accounts) to the role, then validate again."
                ),
                details={"missing_permissions": MISSING_ACTIONS},
            )
            checks.append(ValidationCheck("permissions", "IAM permissions", "failed", str(err)))
            return CredentialValidationResult(
                valid=False, checks=checks, missing_permissions=list(MISSING_ACTIONS), error=err.to_dict()
            )
        checks.append(
            ValidationCheck(
                "permissions", "IAM permissions", "passed", f"All {len(PROVISIONER_ACTIONS)} actions allowed"
            )
        )
        return CredentialValidationResult(valid=True, checks=checks)

    # ---------------------------------------------------------------- networks

    def describe_network(self, account: CloudAccountContext, lookup: NetworkLookup) -> NetworkDetails:
        """Synthesizes the EC2 Describe* responses a real lookup would get, deterministically."""
        self._pause(0.6)
        zones = self.descriptor.region_zones(lookup.region)
        second = 16 + zlib.crc32(lookup.vpc.encode()) % 200
        vpc = (
            None
            if "dead" in lookup.vpc
            else {
                "VpcId": lookup.vpc,
                "CidrBlock": f"10.{second}.0.0/16",
                "State": "available",
                "Tags": [{"Key": "Name", "Value": f"workloads-{lookup.vpc[-4:]}"}],
            }
        )
        subnets: dict[str, dict[str, Any] | None] = {}
        route_tables: dict[str, dict[str, Any] | None] = {}
        for subnet_id in lookup.subnets:
            if "dead" in subnet_id:
                subnets[subnet_id] = None
                continue
            zone_index = int(subnet_id[-1], 16) % len(zones)
            block = zone_index * 5 + zlib.crc32(subnet_id.encode()) % 5
            subnets[subnet_id] = {
                "SubnetId": subnet_id,
                "VpcId": "vpc-0bad0000000000000" if "0bad" in subnet_id else lookup.vpc,
                "CidrBlock": f"10.{second}.{block * 8}.0/21",
                "AvailabilityZone": zones[zone_index],
                "AvailableIpAddressCount": 2043,
                "MapPublicIpOnLaunch": "cafe" in subnet_id,
                "State": "available",
                "Tags": [{"Key": "Name", "Value": f"private-{zones[zone_index][-1]}"}],
            }
            routes: list[dict[str, Any]] = [{"DestinationCidrBlock": f"10.{second}.0.0/16", "GatewayId": "local"}]
            if "beef" not in subnet_id:
                routes.append(
                    {"DestinationCidrBlock": "0.0.0.0/0", "NatGatewayId": f"nat-{stable_digest(subnet_id)[:17]}"}
                )
            route_tables[subnet_id] = {"Routes": routes}
        return aws_network_details(
            lookup=lookup, zones_of_region=zones, vpc=vpc, subnets=subnets, route_tables=route_tables
        )

    # --------------------------------------------------------------- terraform

    def foundation(self, request: InfrastructureRequest) -> tuple[tuple[str, str], ...]:
        network = request.network
        values = {
            "prefix": request.resource_prefix,
            "vpc": network.vpc if network else "?",
            "cidrs": ", ".join(network.cidrs) if network else "?",
        }
        return tuple((address, message.format(**values)) for address, message in FOUNDATION)

    def teardown_messages(self, request: InfrastructureRequest) -> tuple[str, ...]:
        return (
            "Deleting secrets, TLS material and the artifacts bucket",
            "Deleting the instance role and profile",
            "Deleting the security group (the VPC and subnets are the customer's and stay)",
        )

    def node_messages(self, request: InfrastructureRequest, node: NodePlacement) -> tuple[str, str]:
        instance = request.instance_name(node.name)
        subnet = request.network.subnet_for(node.zone).id if request.network else "?"
        return (
            f"Creating EBS volume {instance}-data ({request.storage_gb} GB {request.storage_type}, encrypted) "
            f"in {node.zone}",
            f"Launching EC2 instance {instance} ({request.machine_type}, {subnet}, IMDSv2, no public IP)",
        )

    def instance_id(self, project_id: str, zone: str, instance_name: str) -> str:
        return f"i-{stable_digest(project_id, zone, instance_name)[:17]}"

    def hostname(self, request: InfrastructureRequest, zone: str, instance_name: str, private_ip: str) -> str:
        return f"ip-{private_ip.replace('.', '-')}.{request.region}.compute.internal"

    def outputs(self, request: InfrastructureRequest, nodes: list[ProvisionedNode]) -> dict[str, Any]:
        prefix = request.resource_prefix
        network = request.network
        return {
            "vpc": network.vpc if network else None,
            "subnets": [s.id for s in network.subnets] if network else [],
            "security_group": f"sg-{stable_digest(request.cluster_id, 'sg')[:17]}",
            "instance_profile": f"{prefix}-node",
            "secrets": {
                "elastic_password": f"byoc/{prefix}/elastic-password",
                "ca_certificate": f"byoc/{prefix}/ca-cert",
            },
            "artifacts_bucket": f"{prefix}-art-mock",
            "http_endpoints": [f"https://{n.private_ip}:9200" for n in nodes],
        }
