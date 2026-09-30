"""MOCK_MODE-only endpoints: inject failures into the simulated data plane, and mint VM
identity tokens so the real Go agent can be run against a local control plane."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_platform, get_principal, get_session
from app.api.v1.schemas import FaultRequest, IdentityTokenResponse
from app.application.audit_service import record_audit
from app.application.platform import Platform
from app.application.principal import Principal
from app.domain.enums import AuditEvent, AuditStatus
from app.domain.errors import NotFound, NotSupported
from app.domain.rbac import Permission
from app.providers.cloud.mock.dataplane import MockDataPlane
from app.repositories import queries

router = APIRouter(prefix="/mock", tags=["mock mode"])


def _require_simulated(platform: Platform, provider: str) -> None:
    if not platform.settings.mock_mode or not platform.registry.descriptor(provider).simulated:
        raise NotSupported("This endpoint is only available in mock mode.")


def _dataplane(platform: Platform) -> MockDataPlane:
    # The simulated data plane is database rows only; no cloud access is involved.
    return MockDataPlane(platform.settings, platform.session_factory)


@router.post("/clusters/{cluster_id}/faults")
def inject_fault(
    cluster_id: str,
    body: FaultRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> dict[str, Any]:
    principal.require(Permission.CLUSTER_OPERATE)
    cluster = queries.cluster(session, principal.organization_id, cluster_id)
    _require_simulated(platform, cluster.cloud_provider)
    instance = _dataplane(platform).set_fault(str(cluster.id), body.node_name, body.fault)
    if instance is None:
        raise NotFound(f"Node {body.node_name} has no simulated VM.")
    record_audit(
        session,
        organization_id=principal.organization_id,
        principal=principal,
        event=AuditEvent.FAULT_INJECTED,
        resource_type="cluster",
        resource_id=cluster.id,
        resource_name=cluster.name,
        status=AuditStatus.SUCCESS,
        details={"node": body.node_name, "fault": body.fault},
    )
    session.commit()
    return {"node_name": body.node_name, "faults": instance.faults, "instance_status": instance.status}


@router.post("/clusters/{cluster_id}/nodes/{node_name}/identity-token", response_model=IdentityTokenResponse)
def identity_token(
    cluster_id: str,
    node_name: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> IdentityTokenResponse:
    principal.require(Permission.CLUSTER_OPERATE)
    cluster = queries.cluster(session, principal.organization_id, cluster_id)
    node = next((n for n in queries.active_nodes(session, cluster.id) if n.name == node_name), None)
    if node is None or node.instance_name is None:
        raise NotFound(f"Node {node_name} not found.")
    audience = platform.settings.agent_identity_audience
    _require_simulated(platform, cluster.cloud_provider)
    token = platform.registry.descriptor(cluster.cloud_provider).mint_identity_token(
        project_id=cluster.project_id,
        zone=node.zone,
        instance_name=node.instance_name,
        instance_id=node.instance_id or "",
        audience=audience,
    )
    return IdentityTokenResponse(identity_token=token, audience=audience)
