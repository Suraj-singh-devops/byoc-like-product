from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, TimestampMixin, UTCDateTime

_NOT_DELETED = text("deleted_at IS NULL")


class Cluster(IdMixin, TimestampMixin, Base):
    __tablename__ = "clusters"
    __table_args__ = (
        Index(
            "uq_clusters_org_name_active",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=_NOT_DELETED,
            sqlite_where=_NOT_DELETED,
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(63))
    engine: Mapped[str] = mapped_column(String(40))
    engine_version: Mapped[str] = mapped_column(String(20))
    cloud_provider: Mapped[str] = mapped_column(String(20))
    cloud_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("cloud_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Required for new clusters (docs/adr/0013). A cluster created before networks has no network
    # and runs in a dedicated VPC. Environment and network names are also kept in desired_state.
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("environments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    network_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("networks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # GCP project ID, or the AWS account ID.
    project_id: Mapped[str] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(50))
    zone: Mapped[str] = mapped_column(String(50))
    machine_type: Mapped[str] = mapped_column(String(60))
    node_count: Mapped[int] = mapped_column(Integer)
    storage_gb: Mapped[int] = mapped_column(Integer)
    storage_type: Mapped[str] = mapped_column(String(40))
    high_availability: Mapped[bool] = mapped_column(Boolean, default=False)
    # What the platform is doing (set by operations) and how the cluster is doing (set by
    # health evaluation) are stored separately: docs/adr/0008.
    lifecycle_state: Mapped[str] = mapped_column(String(20), index=True)
    health: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    # Latest HealthAssessment.to_dict()
    health_details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    # Latest aggregated metrics, kept on the row so list views stay cheap.
    metrics_summary: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    desired_state: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    actual_state: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    # Incremented on every desired-state change; observed_generation catches up when the
    # control plane has reconciled that change.
    generation: Mapped[int] = mapped_column(Integer, default=1)
    observed_generation: Mapped[int] = mapped_column(Integer, default=0)
    # Prefix for cloud resource names, e.g. "production-search-3f9a".
    resource_prefix: Mapped[str] = mapped_column(String(50))
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_health_check_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ClusterNode(IdMixin, TimestampMixin, Base):
    __tablename__ = "cluster_nodes"
    __table_args__ = (
        Index(
            "uq_cluster_nodes_cluster_name_active",
            "cluster_id",
            "name",
            unique=True,
            postgresql_where=_NOT_DELETED,
            sqlite_where=_NOT_DELETED,
        ),
    )

    cluster_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("clusters.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(63))
    ordinal: Mapped[int] = mapped_column(Integer)
    instance_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    instance_name: Mapped[str | None] = mapped_column(String(63), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    private_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    zone: Mapped[str] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(60))
    # Dedicated layout (docs/adr/0016): master, data or coordinating, with its own machine type.
    # NULL for combined-layout nodes, which use the cluster's machine type.
    node_group: Mapped[str | None] = mapped_column(String(20), nullable=True)
    machine_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(String(20))
    health: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    health_reasons: Mapped[list[str]] = mapped_column(JSONType, default=list)
    health_warnings: Mapped[list[str]] = mapped_column(JSONType, default=list)
    # As reported by the cloud API (RUNNING, TERMINATED, ..., NOT_FOUND).
    instance_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    agent_status: Mapped[str] = mapped_column(String(20), default="NOT_REPORTED")
    engine_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    agent_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    agent_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    agent_registered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    agent_last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_report: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    last_report_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    report_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    bootstrap_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    bootstrap_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
