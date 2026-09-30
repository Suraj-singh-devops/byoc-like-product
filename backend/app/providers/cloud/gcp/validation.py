"""Checks of GCP identifiers entered by users, before any cloud call (shared by the real and the
simulated GCP providers)."""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.domain.errors import ValidationFailed
from app.providers.cloud.base import AccountRegistration, NetworkLookup, Region

GCP_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
SA_EMAIL_RE = re.compile(r"^([a-z][a-z0-9-]{4,28}[a-z0-9])@([a-z][a-z0-9-]{4,28}[a-z0-9])\.iam\.gserviceaccount\.com$")
# Names of networks and subnetworks (RFC 1035 labels).
RESOURCE_NAME_RE = re.compile(r"^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$")


def field_error(field: str, message: str) -> ValidationFailed:
    return ValidationFailed(message, details={"fields": {field: message}})


def check_gcp_account(registration: AccountRegistration) -> AccountRegistration:
    project_id = registration.project_id.strip()
    if not GCP_PROJECT_RE.match(project_id):
        raise field_error("project_id", "Not a valid GCP project ID (6-30 lowercase letters, digits, hyphens).")
    if registration.role_arn:
        raise field_error("role_arn", "A role ARN is only used for AWS accounts.")
    email = (registration.service_account_email or "").strip().lower()
    match = SA_EMAIL_RE.match(email)
    if match is None and "@" in email and not email.endswith(".gserviceaccount.com"):
        raise field_error(
            "service_account_email",
            f"{email} is a user account. The platform impersonates a service account in the project "
            f"(for example db-platform-provisioner@{project_id}.iam.gserviceaccount.com), never a person.",
        )
    if match is None:
        raise field_error(
            "service_account_email", "Enter the service account email (name@project-id.iam.gserviceaccount.com)."
        )
    if match.group(2) != project_id:
        raise field_error(
            "service_account_email",
            f"The service account must belong to project {project_id} "
            f"(an address ending in @{project_id}.iam.gserviceaccount.com).",
        )
    return AccountRegistration(project_id=project_id, service_account_email=email)


def check_gcp_network_lookup(lookup: NetworkLookup, regions: Iterable[Region]) -> NetworkLookup:
    region = lookup.region.strip()
    if region not in {r.name for r in regions}:
        raise field_error("region", f"'{region}' is not an available region.")
    vpc = lookup.vpc.strip()
    if not RESOURCE_NAME_RE.match(vpc):
        raise field_error("vpc", "Enter the VPC network's name, for example prod-vpc.")
    subnets = [s.strip() for s in lookup.subnets if s.strip()]
    if len(subnets) != 1:
        raise field_error(
            "subnets", "Enter exactly one subnet: a GCP subnet is regional and serves every zone of its region."
        )
    if not RESOURCE_NAME_RE.match(subnets[0]):
        raise field_error("subnets", "Enter the subnet's name, for example db-subnet.")
    return NetworkLookup(region=region, vpc=vpc, subnets=(subnets[0],))
