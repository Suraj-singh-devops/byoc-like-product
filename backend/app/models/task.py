from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, TimestampMixin, UTCDateTime


class CloudTask(IdMixin, TimestampMixin, Base):
    """One unit of cloud work handed from a process without cloud access (API, cluster-manager) to
    one with it (terraform-runner, monitoring-worker). PostgreSQL is the record; Redis only wakes
    the worker (docs/adr/0004)."""

    __tablename__ = "cloud_tasks"
    __table_args__ = (Index("ix_cloud_tasks_role_status_created", "role", "status", "created_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    # The operation the task belongs to, if any: its cancellation stops the task at a safe point.
    operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(20))  # terraform | monitoring
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))  # PENDING, RUNNING, SUCCEEDED, FAILED
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    # Progress messages for the operation log, appended by the worker.
    progress: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
