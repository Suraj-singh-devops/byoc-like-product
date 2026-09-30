"""Checks of AWS identifiers entered by users, before any cloud call."""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.domain.errors import ValidationFailed
from app.providers.cloud.base import AccountRegistration, NetworkLookup, Region

ACCOUNT_ID_RE = re.compile(r"^\d{12}$")
# Commercial partition only; the role may have a path (role/path/name).
ROLE_ARN_RE = re.compile(r"^arn:aws:iam::(\d{12}):role/((?:[\w+=,.@-]+/)*[\w+=,.@-]{1,64})$")
VPC_ID_RE = re.compile(r"^vpc-(?:[0-9a-f]{8}|[0-9a-f]{17})$")
SUBNET_ID_RE = re.compile(r"^subnet-(?:[0-9a-f]{8}|[0-9a-f]{17})$")
MAX_SUBNETS = 6


def field_error(field: str, message: str) -> ValidationFailed:
    return ValidationFailed(message, details={"fields": {field: message}})


def check_aws_account(registration: AccountRegistration) -> AccountRegistration:
    account_id = re.sub(r"[\s-]", "", registration.project_id)
    if not ACCOUNT_ID_RE.match(account_id):
        raise field_error("project_id", "Enter the 12-digit AWS account ID.")
    if registration.service_account_email:
        raise field_error("service_account_email", "A service account is only used for GCP projects.")
    arn = (registration.role_arn or "").strip()
    match = ROLE_ARN_RE.match(arn)
    if match is None:
        raise field_error("role_arn", "Enter the role's ARN (arn:aws:iam::<account-id>:role/<role-name>).")
    if match.group(1) != account_id:
        raise field_error(
            "role_arn", f"The role must be in account {account_id} (an ARN starting arn:aws:iam::{account_id}:role/)."
        )
    return AccountRegistration(project_id=account_id, role_arn=arn)


def check_aws_network_lookup(lookup: NetworkLookup, regions: Iterable[Region]) -> NetworkLookup:
    region = lookup.region.strip()
    if region not in {r.name for r in regions}:
        raise field_error("region", f"'{region}' is not an available region.")
    vpc = lookup.vpc.strip().lower()
    if not VPC_ID_RE.match(vpc):
        raise field_error("vpc", "Enter the VPC ID, for example vpc-0a1b2c3d4e5f67890.")
    subnets = list(dict.fromkeys(s.strip().lower() for s in lookup.subnets if s.strip()))
    if not 1 <= len(subnets) <= MAX_SUBNETS:
        raise field_error("subnets", f"Enter between 1 and {MAX_SUBNETS} subnet IDs of the VPC.")
    bad = [s for s in subnets if not SUBNET_ID_RE.match(s)]
    if bad:
        raise field_error("subnets", f"Not a subnet ID: {', '.join(bad)} (for example subnet-0a1b2c3d4e5f67890).")
    return NetworkLookup(region=region, vpc=vpc, subnets=tuple(subnets))
