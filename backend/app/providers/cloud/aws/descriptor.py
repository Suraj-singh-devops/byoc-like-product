"""AWS as every process may know it (docs/adr/0014). Simulated only until the AWS track."""

from __future__ import annotations

from typing import Any

from app.config.settings import Settings
from app.providers.cloud.aws.catalog import DEFAULT_INSTANCE_TYPE, INSTANCE_TYPES, REGIONS, STORAGE_TYPES
from app.providers.cloud.aws.permissions import aws_onboarding
from app.providers.cloud.aws.validation import check_aws_account, check_aws_network_lookup
from app.providers.cloud.base import AccountRegistration, InstanceIdentity, NetworkLookup
from app.providers.cloud.descriptor import CloudDescriptor


class AwsDescriptor(CloudDescriptor):
    name = "aws"
    display_name = "Amazon Web Services (simulated)"
    auth_type = "assume_role"
    default_machine_type = DEFAULT_INSTANCE_TYPE
    simulated = True
    regions = REGIONS
    machine_types = INSTANCE_TYPES
    storage_catalog = STORAGE_TYPES

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    def check_account(self, registration: AccountRegistration) -> AccountRegistration:
        return check_aws_account(registration)

    def check_network_lookup(self, lookup: NetworkLookup) -> NetworkLookup:
        return check_aws_network_lookup(lookup, self.regions)

    def onboarding(self, organization_key: str, external_id: str) -> dict[str, Any]:
        return aws_onboarding(organization_key, external_id)

    def verify_instance_identity(self, project_id: str, token: str, audience: str) -> InstanceIdentity:
        # The real AWS path verifies the signed EC2 instance identity document (AWS track A4).
        return self._verify_mock_identity(project_id, token, audience)
