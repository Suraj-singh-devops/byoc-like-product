"""What every process may know about a cloud without reaching into a customer account.

A descriptor holds the provider's identity, its static catalog (regions, zones, machine and
storage types), the checks of identifiers users enter, the onboarding instructions, and the
verification of VM identity tokens (signed with public keys, so no customer access is needed).

The API and the cluster-manager only ever use descriptors; reaching a customer account needs a
CloudProvider, which exists only in the terraform-runner and the monitoring-worker
(docs/adr/0004).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config.settings import Settings
from app.domain.errors import AuthenticationFailed, ValidationFailed
from app.infrastructure.security import derive_key
from app.providers.cloud.base import (
    AccountRegistration,
    InstanceIdentity,
    MachineType,
    NetworkLookup,
    Region,
    StorageType,
)

MOCK_ISSUER = "byoc-mock-metadata-server"


class CloudDescriptor(ABC):
    name: str
    display_name: str
    auth_type: str
    default_machine_type: str
    simulated: bool
    regions: tuple[Region, ...]
    machine_types: tuple[MachineType, ...]
    storage_catalog: tuple[StorageType, ...]

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------ catalog

    def list_regions(self) -> list[Region]:
        return list(self.regions)

    def region_zones(self, region: str) -> list[str]:
        found = next((r for r in self.regions if r.name == region), None)
        return list(found.zones) if found else []

    def list_machine_types(self, zone: str) -> list[MachineType]:
        return list(self.machine_types)

    def storage_types(self) -> list[StorageType]:
        return list(self.storage_catalog)

    def validate_placement(
        self, region: str, zone: str, machine_type: str, known_zones: list[str] | tuple[str, ...] | None = None
    ) -> MachineType:
        """Static check at request time; the terraform-runner verifies placement in the real account.

        ``known_zones`` are the zones of a registered network, looked up in the customer's cloud
        (docs/adr/0013). They are authoritative: the static region list is only a fallback and
        does not have to list every region."""
        if known_zones:
            if zone not in known_zones:
                raise ValidationFailed(
                    f"Zone {zone} is not available in this network.",
                    details={"fields": {"zone": f"Choose one of {', '.join(known_zones)}."}},
                )
        else:
            region_info = next((r for r in self.regions if r.name == region), None)
            if region_info is None:
                raise ValidationFailed(
                    f"Region {region} is not available.", details={"fields": {"region": "Unknown region."}}
                )
            if zone not in region_info.zones:
                raise ValidationFailed(
                    f"Zone {zone} does not exist in region {region}.",
                    details={"fields": {"zone": f"Choose one of {', '.join(region_info.zones)}."}},
                )
        machine = next((m for m in self.machine_types if m.name == machine_type), None)
        if machine is None:
            raise ValidationFailed(
                f"Machine type {machine_type} is not available in {zone}.",
                details={"fields": {"machine_type": "Unknown machine type."}},
            )
        return machine

    def placement_zones(self, region: str, primary_zone: str, spread: bool) -> list[str]:
        """Zones of a cluster created before registered networks (docs/adr/0013)."""
        if not spread:
            return [primary_zone]
        others = sorted(z for z in self.region_zones(region) if z != primary_zone)
        return [primary_zone, *others[:2]]

    # ------------------------------------------------------------ user input

    @abstractmethod
    def check_account(self, registration: AccountRegistration) -> AccountRegistration: ...

    @abstractmethod
    def check_network_lookup(self, lookup: NetworkLookup) -> NetworkLookup: ...

    @abstractmethod
    def onboarding(self, organization_key: str, external_id: str) -> dict[str, Any]: ...

    # ------------------------------------------------------------ VM identity

    @abstractmethod
    def verify_instance_identity(self, project_id: str, token: str, audience: str) -> InstanceIdentity:
        """Verify the identity document an agent presents when it registers."""

    # Simulated clouds sign identity tokens with a key derived from SECRET_KEY, standing in for the
    # cloud's metadata server.

    def _mock_key(self) -> bytes:
        return derive_key(self.settings.secret_key, "mock-identity")

    def mint_identity_token(
        self, *, project_id: str, zone: str, instance_name: str, instance_id: str, audience: str
    ) -> str:
        if not self.simulated:
            raise AuthenticationFailed("Identity tokens are minted only by simulated clouds.")
        now = datetime.now(UTC)
        claims = {
            "iss": MOCK_ISSUER,
            "aud": audience,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "instance": {
                "provider": self.name,
                "project_id": project_id,
                "zone": zone,
                "instance_name": instance_name,
                "instance_id": instance_id,
            },
        }
        return jwt.encode(claims, self._mock_key(), algorithm="HS256")

    def _verify_mock_identity(self, project_id: str, token: str, audience: str) -> InstanceIdentity:
        try:
            claims = jwt.decode(token, self._mock_key(), algorithms=["HS256"], audience=audience, issuer=MOCK_ISSUER)
        except jwt.PyJWTError as exc:
            raise AuthenticationFailed("The instance identity token is invalid.", reason=str(exc)) from exc
        instance = claims.get("instance") or {}
        if instance.get("provider") != self.name or instance.get("project_id") != project_id:
            raise AuthenticationFailed("The instance belongs to a different project or account.")
        return InstanceIdentity(
            project_id=instance["project_id"],
            zone=instance["zone"],
            instance_name=instance["instance_name"],
            instance_id=instance["instance_id"],
        )
