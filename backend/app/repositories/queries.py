"""Organization-scoped data access.

Every lookup of a tenant resource takes the caller's organization ID and filters on it, so
a resource of another organization is indistinguishable from one that does not exist (404).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.domain.enums import MUTATING_OPERATION_TYPES
from app.domain.errors import NotFound
from app.domain.states import ACTIVE_OPERATION_STATUSES
from app.models import (
    AuditLog,
    CloudAccount,
    Cluster,
    ClusterEvent,
    ClusterNode,
    Environment,
    MetricSample,
    Network,
    Operation,
    Organization,
    OrganizationMember,
    User,
)


def _as_uuid(value: uuid.UUID | str, what: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise NotFound(f"{what} not found.") from exc


# ------------------------------------------------------------------ users/orgs


def user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email))


def memberships_of(session: Session, user_id: uuid.UUID) -> Sequence[OrganizationMember]:
    return session.scalars(
        select(OrganizationMember).where(OrganizationMember.user_id == user_id).order_by(OrganizationMember.created_at)
    ).all()


def membership(session: Session, organization_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMember | None:
    return session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == organization_id, OrganizationMember.user_id == user_id
        )
    )


def members_of(session: Session, organization_id: uuid.UUID) -> Sequence[OrganizationMember]:
    return session.scalars(
        select(OrganizationMember)
        .where(OrganizationMember.organization_id == organization_id)
        .order_by(OrganizationMember.created_at)
    ).all()


def owner_count(session: Session, organization_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(OrganizationMember.organization_id == organization_id, OrganizationMember.role == "OWNER")
        )
        or 0
    )


def organization(session: Session, organization_id: uuid.UUID) -> Organization:
    org = session.get(Organization, organization_id)
    if org is None:
        raise NotFound("Organization not found.")
    return org


def emails_by_user_id(session: Session, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    ids = {u for u in user_ids if u is not None}
    if not ids:
        return {}
    return {u.id: u.email for u in session.scalars(select(User).where(User.id.in_(ids)))}


# -------------------------------------------------------------- cloud accounts


def cloud_account(session: Session, organization_id: uuid.UUID, account_id: uuid.UUID | str) -> CloudAccount:
    account = session.scalar(
        select(CloudAccount).where(
            CloudAccount.id == _as_uuid(account_id, "Cloud account"),
            CloudAccount.organization_id == organization_id,
        )
    )
    if account is None:
        raise NotFound("Cloud account not found.")
    return account


def cloud_accounts(session: Session, organization_id: uuid.UUID) -> Sequence[CloudAccount]:
    return session.scalars(
        select(CloudAccount).where(CloudAccount.organization_id == organization_id).order_by(CloudAccount.created_at)
    ).all()


def cluster_counts_by_account(session: Session, organization_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = session.execute(
        select(Cluster.cloud_account_id, func.count())
        .where(Cluster.organization_id == organization_id, Cluster.deleted_at.is_(None))
        .group_by(Cluster.cloud_account_id)
    ).all()
    return {row[0]: int(row[1]) for row in rows if row[0] is not None}


def network_counts_by_account(session: Session, organization_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = session.execute(
        select(Network.cloud_account_id, func.count())
        .where(Network.organization_id == organization_id)
        .group_by(Network.cloud_account_id)
    ).all()
    return {row[0]: int(row[1]) for row in rows}


# ---------------------------------------------------------------- environments


def environment(session: Session, organization_id: uuid.UUID, environment_id: uuid.UUID | str) -> Environment:
    found = session.scalar(
        select(Environment).where(
            Environment.id == _as_uuid(environment_id, "Environment"),
            Environment.organization_id == organization_id,
        )
    )
    if found is None:
        raise NotFound("Environment not found.")
    return found


def environments(session: Session, organization_id: uuid.UUID) -> Sequence[Environment]:
    return session.scalars(
        select(Environment).where(Environment.organization_id == organization_id).order_by(Environment.created_at)
    ).all()


def environment_name_taken(session: Session, organization_id: uuid.UUID, name: str) -> bool:
    return (
        session.scalar(
            select(Environment.id).where(Environment.organization_id == organization_id, Environment.name == name)
        )
        is not None
    )


def cluster_counts_by_environment(session: Session, organization_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = session.execute(
        select(Cluster.environment_id, func.count())
        .where(Cluster.organization_id == organization_id, Cluster.deleted_at.is_(None))
        .group_by(Cluster.environment_id)
    ).all()
    return {row[0]: int(row[1]) for row in rows if row[0] is not None}


# -------------------------------------------------------------------- networks


def network(session: Session, organization_id: uuid.UUID, network_id: uuid.UUID | str) -> Network:
    found = session.scalar(
        select(Network).where(
            Network.id == _as_uuid(network_id, "Network"),
            Network.organization_id == organization_id,
        )
    )
    if found is None:
        raise NotFound("Network not found.")
    return found


def networks(
    session: Session,
    organization_id: uuid.UUID,
    *,
    environment_id: uuid.UUID | None = None,
    provider: str | None = None,
) -> Sequence[Network]:
    stmt = select(Network).where(Network.organization_id == organization_id)
    if environment_id is not None:
        stmt = stmt.where(Network.environment_id == environment_id)
    if provider:
        stmt = stmt.where(Network.provider == provider)
    return session.scalars(stmt.order_by(Network.created_at)).all()


def cluster_counts_by_network(session: Session, organization_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """Clusters that are not deleted, per network. A network in use cannot be removed."""
    rows = session.execute(
        select(Cluster.network_id, func.count())
        .where(Cluster.organization_id == organization_id, Cluster.deleted_at.is_(None))
        .group_by(Cluster.network_id)
    ).all()
    return {row[0]: int(row[1]) for row in rows if row[0] is not None}


# -------------------------------------------------------------------- clusters


def cluster(
    session: Session,
    organization_id: uuid.UUID,
    cluster_id: uuid.UUID | str,
    *,
    include_deleted: bool = False,
    for_update: bool = False,
) -> Cluster:
    """``for_update`` locks the row until commit, so concurrent mutation requests serialize."""
    stmt = select(Cluster).where(
        Cluster.id == _as_uuid(cluster_id, "Cluster"), Cluster.organization_id == organization_id
    )
    if not include_deleted:
        stmt = stmt.where(Cluster.deleted_at.is_(None))
    if for_update:
        stmt = stmt.with_for_update()
    found = session.scalar(stmt)
    if found is None:
        raise NotFound("Cluster not found.")
    return found


def clusters(
    session: Session,
    organization_id: uuid.UUID,
    *,
    include_deleted: bool = False,
    environment_id: uuid.UUID | None = None,
) -> Sequence[Cluster]:
    stmt = select(Cluster).where(Cluster.organization_id == organization_id)
    if not include_deleted:
        stmt = stmt.where(Cluster.deleted_at.is_(None))
    if environment_id is not None:
        stmt = stmt.where(Cluster.environment_id == environment_id)
    return session.scalars(stmt.order_by(Cluster.created_at.desc())).all()


def cluster_name_taken(session: Session, organization_id: uuid.UUID, name: str) -> bool:
    return (
        session.scalar(
            select(Cluster.id).where(
                Cluster.organization_id == organization_id, Cluster.name == name, Cluster.deleted_at.is_(None)
            )
        )
        is not None
    )


def active_nodes(session: Session, cluster_id: uuid.UUID) -> list[ClusterNode]:
    rows = session.scalars(
        select(ClusterNode).where(ClusterNode.cluster_id == cluster_id, ClusterNode.deleted_at.is_(None))
    ).all()
    return sorted(rows, key=lambda n: n.ordinal)


def active_mutation(session: Session, cluster_id: uuid.UUID) -> Operation | None:
    return session.scalar(
        select(Operation).where(
            Operation.cluster_id == cluster_id,
            Operation.status.in_([s.value for s in ACTIVE_OPERATION_STATUSES]),
            Operation.operation_type.in_([t.value for t in MUTATING_OPERATION_TYPES]),
        )
    )


def active_operations(session: Session, cluster_id: uuid.UUID) -> list[Operation]:
    """Every PENDING or running operation of a cluster, oldest first."""
    rows = session.scalars(
        select(Operation).where(
            Operation.cluster_id == cluster_id,
            Operation.status.in_([s.value for s in ACTIVE_OPERATION_STATUSES]),
        )
    ).all()
    return sorted(rows, key=lambda o: o.created_at)


def active_delete(session: Session, cluster_id: uuid.UUID) -> Operation | None:
    return session.scalar(
        select(Operation).where(
            Operation.cluster_id == cluster_id,
            Operation.operation_type == "DELETE_CLUSTER",
            Operation.status.in_([s.value for s in ACTIVE_OPERATION_STATUSES]),
        )
    )


def active_operations_by_cluster(session: Session, organization_id: uuid.UUID) -> dict[uuid.UUID, Operation]:
    rows = session.scalars(
        select(Operation).where(
            Operation.organization_id == organization_id,
            Operation.status.in_([s.value for s in ACTIVE_OPERATION_STATUSES]),
        )
    ).all()
    result: dict[uuid.UUID, Operation] = {}
    for op in sorted(rows, key=lambda o: o.created_at):
        if op.cluster_id is not None:
            result[op.cluster_id] = op
    return result


def cluster_events(session: Session, cluster_id: uuid.UUID, limit: int = 50) -> Sequence[ClusterEvent]:
    return session.scalars(
        select(ClusterEvent)
        .where(ClusterEvent.cluster_id == cluster_id)
        .order_by(ClusterEvent.created_at.desc())
        .limit(limit)
    ).all()


def org_events(session: Session, organization_id: uuid.UUID, limit: int = 20) -> Sequence[ClusterEvent]:
    return session.scalars(
        select(ClusterEvent)
        .where(ClusterEvent.organization_id == organization_id)
        .order_by(ClusterEvent.created_at.desc())
        .limit(limit)
    ).all()


def metric_samples(session: Session, cluster_id: uuid.UUID, since: datetime) -> Sequence[MetricSample]:
    return session.scalars(
        select(MetricSample)
        .where(MetricSample.cluster_id == cluster_id, MetricSample.captured_at >= since)
        .order_by(MetricSample.captured_at)
    ).all()


# ------------------------------------------------------------------ operations


def operation(session: Session, organization_id: uuid.UUID, operation_id: uuid.UUID | str) -> Operation:
    found = session.scalar(
        select(Operation).where(
            Operation.id == _as_uuid(operation_id, "Operation"), Operation.organization_id == organization_id
        )
    )
    if found is None:
        raise NotFound("Operation not found.")
    return found


def paginate(session: Session, stmt: Select, limit: int, offset: int) -> tuple[list, int]:
    total = int(session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
    items = list(session.scalars(stmt.limit(limit).offset(offset)).all())
    return items, total


def operations_query(
    organization_id: uuid.UUID,
    *,
    cluster_id: uuid.UUID | None = None,
    status: str | None = None,
    operation_type: str | None = None,
) -> Select:
    stmt = select(Operation).where(Operation.organization_id == organization_id)
    if cluster_id is not None:
        stmt = stmt.where(Operation.cluster_id == cluster_id)
    if status:
        stmt = stmt.where(Operation.status == status)
    if operation_type:
        stmt = stmt.where(Operation.operation_type == operation_type)
    return stmt.order_by(Operation.created_at.desc())


def cluster_names(session: Session, cluster_ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    ids = {c for c in cluster_ids if c is not None}
    if not ids:
        return {}
    return {c.id: c.name for c in session.scalars(select(Cluster).where(Cluster.id.in_(ids)))}


def audit_query(
    organization_id: uuid.UUID,
    *,
    action: str | None = None,
    status: str | None = None,
    user: str | None = None,
    resource_id: str | None = None,
) -> Select:
    stmt = select(AuditLog).where(AuditLog.organization_id == organization_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if status:
        stmt = stmt.where(AuditLog.status == status)
    if user:
        stmt = stmt.where(AuditLog.user_email.ilike(f"%{user.strip().lower()}%"))
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    return stmt.order_by(AuditLog.created_at.desc())
