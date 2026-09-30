from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, TimestampMixin, UTCDateTime

# At most one active infrastructure-changing operation per cluster, plus at most one active
# delete: a delete may wait behind the operation it pre-empts, and the claim does not start it
# until that operation has stopped (docs/adr/0010). The application checks this too; the
# indexes make it race-proof.
_ACTIVE = "status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')"
_ACTIVE_MUTATION = text(f"{_ACTIVE} AND operation_type NOT IN ('HEALTH_CHECK', 'DELETE_CLUSTER')")
_ACTIVE_DELETE = text(f"{_ACTIVE} AND operation_type = 'DELETE_CLUSTER'")
_HAS_IDEMPOTENCY_KEY = text("idempotency_key IS NOT NULL")


class Operation(IdMixin, TimestampMixin, Base):
    __tablename__ = "operations"
    __table_args__ = (
        Index(
            "uq_operations_active_mutation",
            "cluster_id",
            unique=True,
            postgresql_where=_ACTIVE_MUTATION,
            sqlite_where=_ACTIVE_MUTATION,
        ),
        Index(
            "uq_operations_active_delete",
            "cluster_id",
            unique=True,
            postgresql_where=_ACTIVE_DELETE,
            sqlite_where=_ACTIVE_DELETE,
        ),
        Index(
            "uq_operations_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=_HAS_IDEMPOTENCY_KEY,
            sqlite_where=_HAS_IDEMPOTENCY_KEY,
        ),
        Index("ix_operations_org_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id", ondelete="CASCADE"))
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("clusters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    operation_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), index=True)
    current_step: Mapped[str | None] = mapped_column(String(60), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Parameters, step timeline, results and structured error details.
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_enqueued_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class AgentCommand(IdMixin, TimestampMixin, Base):
    """An approved lifecycle action queued for a node agent (delivered on heartbeat)."""

    __tablename__ = "agent_commands"

    cluster_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("clusters.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("cluster_nodes.id", ondelete="CASCADE"), index=True)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    command: Mapped[str] = mapped_column(String(60))
    args: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
