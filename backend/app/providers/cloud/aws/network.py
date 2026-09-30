"""AWS network lookup (docs/adr/0013): turns EC2 DescribeVpcs, DescribeSubnets and
DescribeRouteTables responses into NetworkDetails. The simulated provider feeds it synthetic
responses, so both run the same checks."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.domain.errors import CloudProviderError
from app.providers.cloud.base import NetworkDetails, NetworkLookup, SubnetInfo, ValidationCheck

NO_ROUTE_WARNING = (
    "{subnet} has no route to a NAT gateway. The nodes download Elasticsearch from artifacts.elastic.co and "
    "need outbound HTTPS through a NAT gateway or a proxy."
)
IGW_WARNING = (
    "{subnet} routes 0.0.0.0/0 to an internet gateway. Nodes get no public IP, so they cannot reach the "
    "internet through it; use a NAT gateway."
)
PUBLIC_IP_WARNING = (
    "{subnet} assigns public IPv4 addresses on launch. The platform launches nodes without public IPs regardless."
)


def name_tag(resource: dict[str, Any]) -> str | None:
    return next((t.get("Value") for t in resource.get("Tags") or [] if t.get("Key") == "Name"), None)


def default_route_target(route_table: dict[str, Any] | None) -> str | None:
    for route in (route_table or {}).get("Routes") or []:
        if route.get("DestinationCidrBlock") == "0.0.0.0/0" and route.get("State", "active") == "active":
            return route.get("NatGatewayId") or route.get("GatewayId") or route.get("NetworkInterfaceId")
    return None


def aws_network_details(
    *,
    lookup: NetworkLookup,
    zones_of_region: Iterable[str],
    vpc: dict[str, Any] | None,
    subnets: dict[str, dict[str, Any] | None],
    route_tables: dict[str, dict[str, Any] | None],
) -> NetworkDetails:
    """``subnets`` and ``route_tables`` are keyed by the requested subnet IDs; ``route_tables``
    holds each subnet's effective route table (its own, or the VPC's main table)."""
    checks: list[ValidationCheck] = []

    def failed(key: str, label: str, err: CloudProviderError) -> NetworkDetails:
        checks.append(ValidationCheck(key, label, "failed", str(err)))
        return NetworkDetails(
            valid=False,
            region=lookup.region,
            vpc=lookup.vpc,
            vpc_name=lookup.vpc,
            vpc_cidrs=[],
            subnets=[],
            zones=[],
            checks=checks,
            error=err.to_dict(),
        )

    if vpc is None or vpc.get("State", "available") != "available":
        return failed(
            "vpc",
            "VPC",
            CloudProviderError(
                f"VPC {lookup.vpc} was not found in {lookup.region}.",
                code="NETWORK_NOT_FOUND",
                reason="DescribeVpcs returned no available VPC with this ID.",
                suggested_action="Check the VPC ID and the region.",
            ),
        )
    vpc_name = name_tag(vpc) or lookup.vpc
    cidrs = [a["CidrBlock"] for a in vpc.get("CidrBlockAssociationSet") or [] if a.get("CidrBlock")]
    cidrs = cidrs or [vpc["CidrBlock"]]
    checks.append(ValidationCheck("vpc", "VPC", "passed", f"{lookup.vpc} ({vpc_name}, {', '.join(cidrs)})"))

    region_zones = set(zones_of_region)
    infos: list[SubnetInfo] = []
    by_zone: dict[str, str] = {}
    for subnet_id in lookup.subnets:
        body = subnets.get(subnet_id)
        if body is None:
            return failed(
                "subnets",
                "Subnets",
                CloudProviderError(
                    f"Subnet {subnet_id} was not found in {lookup.region}.",
                    code="SUBNET_NOT_FOUND",
                    suggested_action="Check the subnet IDs and the region.",
                ),
            )
        if body.get("VpcId") != lookup.vpc:
            return failed(
                "subnets",
                "Subnets",
                CloudProviderError(
                    f"Subnet {subnet_id} belongs to {body.get('VpcId')}, not {lookup.vpc}.",
                    code="SUBNET_NOT_IN_VPC",
                    suggested_action=f"Choose subnets of {lookup.vpc}.",
                ),
            )
        zone = str(body.get("AvailabilityZone"))
        if zone not in region_zones:
            return failed(
                "subnets",
                "Subnets",
                CloudProviderError(
                    f"Subnet {subnet_id} is in {zone}, outside {lookup.region}.", code="SUBNET_NOT_IN_REGION"
                ),
            )
        if zone in by_zone:
            return failed(
                "subnets",
                "Subnets",
                CloudProviderError(
                    f"Subnets {by_zone[zone]} and {subnet_id} are both in {zone}.",
                    code="DUPLICATE_ZONE",
                    suggested_action="Register at most one subnet per availability zone.",
                ),
            )
        by_zone[zone] = subnet_id
        infos.append(
            SubnetInfo(
                id=subnet_id,
                name=name_tag(body) or subnet_id,
                cidr=str(body["CidrBlock"]),
                zone=zone,
                available_ips=int(body.get("AvailableIpAddressCount") or 0),
            )
        )
    summary = ", ".join(f"{s.id} ({s.zone}, {s.cidr}, {s.available_ips} free)" for s in infos)
    checks.append(ValidationCheck("subnets", "Subnets", "passed", summary))

    warnings: list[str] = []
    for info in infos:
        target = default_route_target(route_tables.get(info.id))
        if target and target.startswith("nat-"):
            continue
        warnings.append(
            (IGW_WARNING if target and target.startswith("igw-") else NO_ROUTE_WARNING).format(subnet=info.id)
        )
    checks.append(
        ValidationCheck(
            "egress",
            "Outbound access",
            "warning" if warnings else "passed",
            " ".join(warnings) or "Every subnet routes 0.0.0.0/0 through a NAT gateway",
        )
    )
    public = [s for s in lookup.subnets if (subnets.get(s) or {}).get("MapPublicIpOnLaunch")]
    for subnet_id in public:
        warnings.append(PUBLIC_IP_WARNING.format(subnet=subnet_id))
    if public:
        checks.append(
            ValidationCheck(
                "public_ip", "Public addresses", "warning", PUBLIC_IP_WARNING.format(subnet=", ".join(public))
            )
        )

    return NetworkDetails(
        valid=True,
        region=lookup.region,
        vpc=lookup.vpc,
        vpc_name=vpc_name,
        vpc_cidrs=cidrs,
        subnets=infos,
        zones=sorted(by_zone),
        checks=checks,
        warnings=warnings,
    )
