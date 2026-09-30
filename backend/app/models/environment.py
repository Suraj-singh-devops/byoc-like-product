from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, TimestampMixin, UTCDateTime


class Environment(IdMixin, TimestampMixin, Base):
    """A group of networks and clusters, such as test or production (docs/adr/0013)."""

    __tablename__ = "environments"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(40))
    type: Mapped[str] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class Network(IdMixin, TimestampMixin, Base):
    """A platform record pointing at an existing VPC and subnet(s) in a customer cloud account.

    The platform never creates or changes the network itself (docs/adr/0013). Only the
    identifiers come from the user; ``details`` comes from a lookup in the customer's cloud.
    """

    __tablename__ = "networks"
    __table_args__ = (UniqueConstraint("environment_id", "name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("environments.id", ondelete="CASCADE"), index=True
    )
    # Removing a cloud account that networks use is refused by the application.
    cloud_account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("cloud_accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(63))
    provider: Mapped[str] = mapped_column(String(20))
    region: Mapped[str] = mapped_column(String(50))
    # As entered. GCP: VPC network name and [subnet name]; AWS: VPC ID and subnet IDs.
    vpc: Mapped[str] = mapped_column(String(255))
    subnets: Mapped[list[str]] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(20))
    # NetworkDetails.to_dict() of the latest lookup: resource paths, ranges, zones, checks, warnings.
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Set on every successful lookup; a later failure then means UNAVAILABLE, not FAILED.
    last_available_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
