from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_platform, get_principal, get_session
from app.api.v1.schemas import (
    CloudAccountCreateRequest,
    CloudAccountOut,
    CloudCatalogOut,
    CloudOnboardingOut,
    CloudProviderOut,
    MachineTypeOut,
    RegionOut,
    StorageTypeOut,
)
from app.application.cloud_account_service import CloudAccountService
from app.application.platform import Platform
from app.application.principal import Principal
from app.domain.rbac import Permission
from app.models import CloudAccount
from app.repositories import queries

router = APIRouter(prefix="/cloud-accounts", tags=["cloud accounts"])
providers_router = APIRouter(prefix="/cloud-providers", tags=["cloud accounts"])


def _service(session: Session, platform: Platform, principal: Principal) -> CloudAccountService:
    return CloudAccountService(session, platform, principal)


def _out(session: Session, principal: Principal, account: CloudAccount) -> CloudAccountOut:
    org = principal.organization_id
    return CloudAccountOut.build(
        account,
        queries.cluster_counts_by_account(session, org).get(account.id, 0),
        queries.network_counts_by_account(session, org).get(account.id, 0),
    )


@providers_router.get("", response_model=list[CloudProviderOut])
def list_cloud_providers(
    principal: Principal = Depends(get_principal), platform: Platform = Depends(get_platform)
) -> list[CloudProviderOut]:
    """Clouds this deployment offers (GCP and AWS in mock mode; docs/adr/0014)."""
    principal.require(Permission.CLOUD_ACCOUNT_READ)
    return [
        CloudProviderOut(
            name=c.name,
            display_name=c.display_name,
            auth_type=c.auth_type,
            simulated=c.simulated,
            default_machine_type=c.default_machine_type,
        )
        for c in sorted(platform.registry.descriptors(), key=lambda c: c.name != "gcp")
    ]


@router.get("/onboarding", response_model=CloudOnboardingOut)
def cloud_onboarding(
    provider: str = Query(max_length=20),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> CloudOnboardingOut:
    """What the customer grants: principal, role, permissions and, for AWS, the external ID."""
    return CloudOnboardingOut(**_service(session, platform, principal).onboarding(provider))


@router.post("", response_model=CloudAccountOut, status_code=201)
def create_cloud_account(
    body: CloudAccountCreateRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> CloudAccountOut:
    account = _service(session, platform, principal).create(
        name=body.name,
        provider=body.provider,
        project_id=body.project_id,
        region=body.region,
        service_account_email=body.service_account_email,
        role_arn=body.role_arn,
        validate_now=body.validate_now,
    )
    return CloudAccountOut.build(account)


@router.get("", response_model=list[CloudAccountOut])
def list_cloud_accounts(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> list[CloudAccountOut]:
    accounts, counts = _service(session, platform, principal).list()
    networks = queries.network_counts_by_account(session, principal.organization_id)
    return [CloudAccountOut.build(a, counts.get(a.id, 0), networks.get(a.id, 0)) for a in accounts]


@router.get("/{account_id}", response_model=CloudAccountOut)
def get_cloud_account(
    account_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> CloudAccountOut:
    return _out(session, principal, _service(session, platform, principal).get(account_id))


@router.post("/{account_id}/validate", response_model=CloudAccountOut)
def validate_cloud_account(
    account_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> CloudAccountOut:
    return _out(session, principal, _service(session, platform, principal).validate(account_id))


@router.delete("/{account_id}", status_code=204)
def delete_cloud_account(
    account_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> Response:
    _service(session, platform, principal).delete(account_id)
    return Response(status_code=204)


@router.get("/{account_id}/catalog", response_model=CloudCatalogOut)
def cloud_catalog(
    account_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> CloudCatalogOut:
    service = _service(session, platform, principal)
    account = service.get(account_id)
    cloud = platform.registry.descriptor(account.provider)
    return CloudCatalogOut(
        provider=cloud.name,
        simulated=cloud.simulated,
        default_machine_type=cloud.default_machine_type,
        regions=[RegionOut(name=r.name, zones=list(r.zones), location=r.location) for r in service.regions(account_id)],
        storage_types=[
            StorageTypeOut(name=s.name, description=s.description) for s in service.storage_types(account_id)
        ],
    )


@router.get("/{account_id}/machine-types", response_model=list[MachineTypeOut])
def machine_types(
    account_id: str,
    zone: str = Query(min_length=3, max_length=50),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> list[MachineTypeOut]:
    return [
        MachineTypeOut(name=m.name, vcpus=m.vcpus, memory_gb=m.memory_gb, architecture=m.architecture)
        for m in _service(session, platform, principal).machine_types(account_id, zone)
    ]
