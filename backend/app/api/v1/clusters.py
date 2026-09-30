from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.api.deps import get_platform, get_principal, get_session
from app.api.v1.schemas import (
    ClusterCreateRequest,
    ClusterDetail,
    ClusterScaleRequest,
    ClusterSummary,
    EventOut,
    NodeOut,
    OperationAccepted,
    OperationBrief,
)
from app.application.cluster_service import ClusterCreateInput, ClusterService
from app.application.platform import Platform
from app.application.principal import Principal

router = APIRouter(prefix="/clusters", tags=["clusters"])
IdempotencyKey = Header(default=None, alias="Idempotency-Key", max_length=200)


def _service(session: Session, platform: Platform, principal: Principal) -> ClusterService:
    return ClusterService(session, platform, principal)


@router.post("", response_model=OperationAccepted, status_code=202)
def create_cluster(
    body: ClusterCreateRequest,
    idempotency_key: str | None = IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> OperationAccepted:
    fields = body.model_dump(mode="python")
    for key in ("environment_id", "network_id", "cloud_account_id"):
        fields[key] = str(fields[key]) if fields[key] is not None else None
    data = ClusterCreateInput(**fields)
    cluster, op = _service(session, platform, principal).create(data, idempotency_key)
    return OperationAccepted(cluster_id=cluster.id, operation_id=op.id, lifecycle=cluster.lifecycle_state)


@router.get("", response_model=list[ClusterSummary])
def list_clusters(
    include_deleted: bool = False,
    environment_id: str | None = Query(default=None, max_length=36),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> list[ClusterSummary]:
    clusters = _service(session, platform, principal).list(include_deleted, environment_id)
    return [ClusterSummary.build(c, op) for c, op in clusters]


@router.get("/{cluster_id}", response_model=ClusterDetail)
def get_cluster(
    cluster_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> ClusterDetail:
    view = _service(session, platform, principal).get(cluster_id)
    cluster = view.cluster
    return ClusterDetail(
        **ClusterSummary.fields_from(cluster, view.active_operation),
        cloud_account_id=cluster.cloud_account_id,
        cloud_account_name=view.account.name if view.account else None,
        simulated=platform.registry.descriptor(cluster.cloud_provider).simulated,
        desired_state=cluster.desired_state or {},
        actual_state=cluster.actual_state or {},
        generation=cluster.generation,
        observed_generation=cluster.observed_generation,
        health_details=cluster.health_details or {},
        resource_prefix=cluster.resource_prefix,
        created_by=view.created_by_email,
        nodes=[NodeOut.build(n, cluster.health_details) for n in view.nodes],
        last_health_check_at=cluster.last_health_check_at,
        deleted_at=cluster.deleted_at,
    )


@router.delete("/{cluster_id}", response_model=OperationAccepted, status_code=202)
def delete_cluster(
    cluster_id: str,
    confirm: str | None = Query(
        default=None, max_length=63, description="The cluster's name, to confirm the permanent deletion."
    ),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> OperationAccepted:
    cluster, op = _service(session, platform, principal).delete(cluster_id, confirm)
    return OperationAccepted(cluster_id=cluster.id, operation_id=op.id, lifecycle=cluster.lifecycle_state)


@router.post("/{cluster_id}/scale", response_model=OperationAccepted, status_code=202)
def scale_cluster(
    cluster_id: str,
    body: ClusterScaleRequest,
    idempotency_key: str | None = IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> OperationAccepted:
    cluster, op = _service(session, platform, principal).scale(cluster_id, body.node_count, idempotency_key)
    return OperationAccepted(cluster_id=cluster.id, operation_id=op.id, lifecycle=cluster.lifecycle_state)


@router.get("/{cluster_id}/health")
def cluster_health(
    cluster_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> dict[str, Any]:
    return _service(session, platform, principal).health(cluster_id)


@router.post("/{cluster_id}/health-check", response_model=OperationBrief, status_code=202)
def run_health_check(
    cluster_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> OperationBrief:
    op = _service(session, platform, principal).request_health_check(cluster_id)
    brief = OperationBrief.build(op)
    assert brief is not None
    return brief


@router.get("/{cluster_id}/metrics")
def cluster_metrics(
    cluster_id: str,
    minutes: int = Query(default=60, ge=5, le=24 * 60),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> dict[str, Any]:
    return _service(session, platform, principal).metrics(cluster_id, minutes)


@router.get("/{cluster_id}/nodes", response_model=list[NodeOut])
def cluster_nodes(
    cluster_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> list[NodeOut]:
    cluster, nodes = _service(session, platform, principal).nodes(cluster_id)
    return [NodeOut.build(n, cluster.health_details) for n in nodes]


@router.get("/{cluster_id}/events", response_model=list[EventOut])
def cluster_events(
    cluster_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    platform: Platform = Depends(get_platform),
) -> list[EventOut]:
    return [EventOut.build(e) for e in _service(session, platform, principal).events(cluster_id, limit)]
