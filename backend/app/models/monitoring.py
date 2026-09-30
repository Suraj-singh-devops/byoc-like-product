from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, UTCDateTime, utcnow


class ClusterEvent(IdMixin, Base):
    """Something noteworthy detected about a cluster (failure detection, recovery, lifecycle)."""

    __tablename__ = "cluster_events"
    __table_args__ = (Index("ix_cluster_events_cluster_created", "cluster_id", "created_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("clusters.id", ondelete="CASCADE"))
    node_name: Mapped[str | None] = mapped_column(String(63), nullable=True)
    event_type: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class MetricSample(Base):
    """Cluster-level metrics snapshot, kept for a short retention window (charts)."""

    __tablename__ = "metric_samples"
    __table_args__ = (Index("ix_metric_samples_cluster_captured", "cluster_id", "captured_at"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    cluster_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("clusters.id", ondelete="CASCADE"))
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    values: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
