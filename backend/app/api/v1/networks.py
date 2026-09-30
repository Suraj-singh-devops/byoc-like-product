from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import get_platform, get_principal, get_session
from app.api.v1.schemas import NetworkDetailsOut, NetworkLookupRequest, NetworkOut
from app.application.network_service import NetworkService
from app.application.platform import Platform
from app.application.principal import Principal
from app.models import CloudAccount, Network

router = APIRouter(prefix="/networks", tags=["networks"])


def _out(session: Session, service: NetworkService, network: Network) -> NetworkOut:
    account = session.get(CloudAccount, network.cloud_account_id)
    return NetworkOut.build(network, account.name if account else None, service.cluster_counts().get(network.id, 0))


@router.post("/lookup", response_model=NetworkDetailsOut)
def lookup_network(
    body: NetworkLookupRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> NetworkDetailsOut:
    """Fetch an existing network's details from the cloud without registering it."""
    details = NetworkService(session, platform, principal).lookup(
        cloud_account_id=str(body.cloud_account_id), region=body.region, vpc=body.vpc, subnets=body.subnets
    )
    return NetworkDetailsOut(**details.to_dict())


@router.get("/{network_id}", response_model=NetworkOut)
def get_network(
    network_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> NetworkOut:
    service = NetworkService(session, platform, principal)
    return _out(session, service, service.get(network_id))


@router.post("/{network_id}/validate", response_model=NetworkOut)
def validate_network(
    network_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> NetworkOut:
    service = NetworkService(session, platform, principal)
    return _out(session, service, service.validate(network_id))


@router.delete("/{network_id}", status_code=204)
def delete_network(
    network_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> Response:
    NetworkService(session, platform, principal).delete(network_id)
    return Response(status_code=204)
