"""Google Cloud as every process may know it (see app/providers/cloud/descriptor.py)."""

from __future__ import annotations

from typing import Any

from app.config.settings import Settings
from app.domain.errors import AuthenticationFailed, ValidationFailed
from app.providers.cloud.base import AccountRegistration, InstanceIdentity, NetworkLookup
from app.providers.cloud.descriptor import CloudDescriptor
from app.providers.cloud.gcp.catalog import MACHINE_TYPES, REGIONS, STORAGE_TYPES
from app.providers.cloud.gcp.permissions import gcp_onboarding
from app.providers.cloud.gcp.validation import check_gcp_account, check_gcp_network_lookup


class GcpDescriptor(CloudDescriptor):
    name = "gcp"
    default_machine_type = "e2-standard-4"
    regions = REGIONS
    machine_types = MACHINE_TYPES
    storage_catalog = STORAGE_TYPES

    def __init__(self, settings: Settings, *, simulated: bool) -> None:
        super().__init__(settings)
        self.simulated = simulated
        self.display_name = "Google Cloud (simulated)" if simulated else "Google Cloud"
        # Development only (docs/adr/0015): the developer's own credentials, no service account.
        self.local_credentials = settings.dev_local_credentials and not simulated
        self.auth_type = "local_credentials" if self.local_credentials else "impersonation"

    def check_account(self, registration: AccountRegistration) -> AccountRegistration:
        if not self.local_credentials:
            return check_gcp_account(registration)
        project_id = registration.project_id.strip()
        if project_id not in self.settings.dev_projects:
            raise ValidationFailed(
                f"Project {project_id} is not in DEV_ALLOWED_PROJECTS.",
                details={"fields": {"project_id": "Local-credential mode only reaches the allowlisted projects."}},
            )
        return AccountRegistration(project_id=project_id)

    def check_network_lookup(self, lookup: NetworkLookup) -> NetworkLookup:
        return check_gcp_network_lookup(lookup, self.regions)

    def onboarding(self, organization_key: str, external_id: str) -> dict[str, Any]:
        return gcp_onboarding(organization_key)

    def verify_instance_identity(self, project_id: str, token: str, audience: str) -> InstanceIdentity:
        if self.simulated:
            return self._verify_mock_identity(project_id, token, audience)
        # A Google-signed instance identity token: verified with Google's public keys only.
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        try:
            claims = id_token.verify_token(token, Request(), audience=audience)
        except Exception as exc:  # google-auth raises ValueError and friends
            raise AuthenticationFailed("The instance identity token is invalid.", reason=str(exc)[:200]) from exc
        if claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
            raise AuthenticationFailed("The instance identity token was not issued by Google.")
        compute = (claims.get("google") or {}).get("compute_engine") or {}
        if not compute:
            raise AuthenticationFailed(
                "The identity token has no Compute Engine claims.",
                suggested_action="Request the token from the metadata server with format=full.",
            )
        if compute.get("project_id") != project_id:
            raise AuthenticationFailed("The instance belongs to a different project.")
        return InstanceIdentity(
            project_id=str(compute["project_id"]),
            zone=str(compute["zone"]),
            instance_name=str(compute["instance_name"]),
            instance_id=str(compute["instance_id"]),
        )
