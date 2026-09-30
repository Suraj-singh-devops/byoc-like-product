"""GCP network lookup (docs/adr/0013): turns Compute API responses about an existing VPC, subnet
and Cloud Routers into NetworkDetails. The simulated provider feeds it synthetic responses, so
both run the same checks."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from typing import Any

from app.domain.errors import CloudProviderError
from app.providers.cloud.base import NetworkDetails, NetworkLookup, SubnetInfo, ValidationCheck

# Subnet purposes VMs can use. Proxy-only, Private Service Connect and NAT subnets cannot host VMs.
USABLE_PURPOSES = frozenset({"PRIVATE", "PRIVATE_RFC_1918"})
# GCP reserves four addresses in every primary range.
RESERVED_ADDRESSES = 4
NAT_ALL_SUBNETS = frozenset({"ALL_SUBNETWORKS_ALL_IP_RANGES", "ALL_SUBNETWORKS_ALL_PRIMARY_IP_RANGES"})

NO_NAT_WARNING = (
    "No Cloud NAT covers this subnet. The nodes download Elasticsearch from artifacts.elastic.co and "
    "need outbound HTTPS through Cloud NAT or a proxy."
)
NO_PGA_WARNING = (
    "Private Google Access is off. Without it the nodes reach Secret Manager and Cloud Storage only through NAT."
)


def resource_path(link: str) -> str:
    """``https://www.googleapis.com/compute/v1/projects/p/global/networks/n`` -> ``projects/p/global/networks/n``."""
    index = link.find("projects/")
    return link[index:] if index >= 0 else link


def nat_covering(routers: Iterable[dict[str, Any]], network_link: str, subnet_link: str) -> str | None:
    """The ``router/nat`` that gives the subnet outbound access, if any."""
    network, subnet = resource_path(network_link), resource_path(subnet_link)
    for router in routers:
        if resource_path(str(router.get("network", ""))) != network:
            continue
        for nat in router.get("nats") or []:
            mode = nat.get("sourceSubnetworkIpRangesToNat")
            listed = {resource_path(str(s.get("name", ""))) for s in nat.get("subnetworks") or []}
            if mode in NAT_ALL_SUBNETS or (mode == "LIST_OF_SUBNETWORKS" and subnet in listed):
                return f"{router.get('name')}/{nat.get('name')}"
    return None


def gcp_network_details(
    *,
    project: str,
    lookup: NetworkLookup,
    zones: Iterable[str],
    network: dict[str, Any] | None,
    subnet: dict[str, Any] | None,
    routers: Iterable[dict[str, Any]],
) -> NetworkDetails:
    name = lookup.subnets[0]
    vpc_path = f"projects/{project}/global/networks/{lookup.vpc}"
    subnet_path = f"projects/{project}/regions/{lookup.region}/subnetworks/{name}"
    checks: list[ValidationCheck] = []

    def failed(key: str, label: str, err: CloudProviderError) -> NetworkDetails:
        checks.append(ValidationCheck(key, label, "failed", str(err)))
        return NetworkDetails(
            valid=False,
            region=lookup.region,
            vpc=vpc_path,
            vpc_name=lookup.vpc,
            vpc_cidrs=[],
            subnets=[],
            zones=[],
            checks=checks,
            error=err.to_dict(),
        )

    if network is None:
        return failed(
            "vpc",
            "VPC network",
            CloudProviderError(
                f"VPC network {lookup.vpc} was not found in project {project}.",
                code="NETWORK_NOT_FOUND",
                reason="networks.get returned no network with this name.",
                suggested_action=(
                    "Check the network name. Shared VPC host projects are not supported yet; use a network "
                    "in this project."
                ),
            ),
        )
    network_link = str(network.get("selfLink") or vpc_path)
    vpc_path = resource_path(network_link)
    mode = "auto mode" if network.get("autoCreateSubnetworks") else "custom mode"
    checks.append(ValidationCheck("vpc", "VPC network", "passed", f"{lookup.vpc} ({mode})"))

    if subnet is None:
        return failed(
            "subnet",
            "Subnet",
            CloudProviderError(
                f"Subnet {name} was not found in region {lookup.region}.",
                code="SUBNET_NOT_FOUND",
                suggested_action="Check the subnet name and the region.",
            ),
        )
    if resource_path(str(subnet.get("network", ""))) != vpc_path:
        other = resource_path(str(subnet.get("network", ""))).rsplit("/", 1)[-1]
        return failed(
            "subnet",
            "Subnet",
            CloudProviderError(
                f"Subnet {name} belongs to VPC network {other}, not {lookup.vpc}.",
                code="SUBNET_NOT_IN_VPC",
                suggested_action=f"Choose a subnet of {lookup.vpc}, or register {other} instead.",
            ),
        )
    purpose = str(subnet.get("purpose") or "PRIVATE")
    if purpose not in USABLE_PURPOSES or subnet.get("stackType") == "IPV6_ONLY":
        kind = "an IPv6-only" if subnet.get("stackType") == "IPV6_ONLY" else f"a {purpose}"
        return failed(
            "subnet",
            "Subnet",
            CloudProviderError(
                f"Subnet {name} is {kind} subnet; VMs cannot run in it.",
                code="SUBNET_NOT_USABLE",
                suggested_action="Choose a regular (PRIVATE) IPv4 subnet.",
            ),
        )
    cidr = str(subnet["ipCidrRange"])
    usable = max(ipaddress.ip_network(cidr, strict=False).num_addresses - RESERVED_ADDRESSES, 0)
    checks.append(
        ValidationCheck("subnet", "Subnet", "passed", f"{name}: {cidr} in {lookup.region}, {usable} usable addresses")
    )

    warnings: list[str] = []
    nat = nat_covering(routers, network_link, str(subnet.get("selfLink") or subnet_path))
    if nat:
        checks.append(ValidationCheck("egress", "Outbound access", "passed", f"Cloud NAT {nat} covers the subnet"))
    else:
        checks.append(ValidationCheck("egress", "Outbound access", "warning", NO_NAT_WARNING))
        warnings.append(NO_NAT_WARNING)
    if subnet.get("privateIpGoogleAccess"):
        checks.append(ValidationCheck("google_access", "Private Google Access", "passed", "On"))
    else:
        checks.append(ValidationCheck("google_access", "Private Google Access", "warning", NO_PGA_WARNING))
        warnings.append(NO_PGA_WARNING)

    return NetworkDetails(
        valid=True,
        region=lookup.region,
        vpc=vpc_path,
        vpc_name=lookup.vpc,
        vpc_cidrs=[],
        subnets=[SubnetInfo(id=subnet_path, name=name, cidr=cidr, zone=None, available_ips=usable)],
        zones=sorted(zones),
        checks=checks,
        warnings=warnings,
    )
