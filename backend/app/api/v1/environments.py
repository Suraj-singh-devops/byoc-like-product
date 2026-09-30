from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_platform, get_principal, get_session
from app.api.v1.schemas import (
    EnvironmentCreateRequest,
    EnvironmentOut,
    NetworkCreateRequest,
    NetworkOut,
)
from app.application.environment_service import EnvironmentService, EnvironmentView
from app.application.network_service import NetworkService
from app.application.platform import Platform
from app.application.principal import Principal
from app.repositories import queries

router = APIRouter(prefix="/environments", tags=["environments"])


def _out(view: EnvironmentView) -> EnvironmentOut:
    return EnvironmentOut.build(view.environment, view.network_count, view.cluster_count, view.providers)


@router.get("", response_model=list[EnvironmentOut])
def list_environments(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> list[EnvironmentOut]:
    return [_out(v) for v in EnvironmentService(session, platform, principal).list()]


@router.post("", response_model=EnvironmentOut, status_code=201)
def create_environment(
    body: EnvironmentCreateRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> EnvironmentOut:
    service = EnvironmentService(session, platform, principal)
    environment = service.create(name=body.name, type_=body.type, description=body.description)
    return _out(service.get(str(environment.id)))


@router.get("/{environment_id}", response_model=EnvironmentOut)
def get_environment(
    environment_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> EnvironmentOut:
    return _out(EnvironmentService(session, platform, principal).get(environment_id))


@router.delete("/{environment_id}", status_code=204)
def delete_environment(
    environment_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> Response:
    EnvironmentService(session, platform, principal).delete(environment_id)
    return Response(status_code=204)


@router.get("/{environment_id}/networks", response_model=list[NetworkOut])
def list_environment_networks(
    environment_id: str,
    provider: str | None = Query(default=None, max_length=20),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> list[NetworkOut]:
    service = NetworkService(session, platform, principal)
    networks = service.list(environment_id, provider)
    counts = service.cluster_counts()
    accounts = {a.id: a.name for a in queries.cloud_accounts(session, principal.organization_id)}
    return [NetworkOut.build(n, accounts.get(n.cloud_account_id), counts.get(n.id, 0)) for n in networks]


@router.post("/{environment_id}/networks", response_model=NetworkOut, status_code=201)
def register_network(
    environment_id: str,
    body: NetworkCreateRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> NetworkOut:
    network = NetworkService(session, platform, principal).create(
        environment_id=environment_id,
        name=body.name,
        cloud_account_id=str(body.cloud_account_id),
        region=body.region,
        vpc=body.vpc,
        subnets=body.subnets,
    )
    account = queries.cloud_account(session, principal.organization_id, network.cloud_account_id)
    return NetworkOut.build(network, account.name, 0)
