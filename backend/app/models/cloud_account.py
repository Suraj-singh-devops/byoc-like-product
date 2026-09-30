from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, TimestampMixin, UTCDateTime


class CloudAccount(IdMixin, TimestampMixin, Base):
    __tablename__ = "cloud_accounts"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(20))
    # GCP project ID, or the 12-digit AWS account ID.
    project_id: Mapped[str] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(50), nullable=True)
    auth_type: Mapped[str] = mapped_column(String(40))
    # Keyless access; no key material is stored. GCP: the customer's service account the platform
    # impersonates. AWS: the customer's role the platform assumes with the organization's
    # external ID (docs/adr/0014).
    service_account_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    role_arn: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(30))
    validation_result: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Set on every successful validation; a later failure then means DISCONNECTED, not FAILED.
    last_connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
