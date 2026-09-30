from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, TimestampMixin, UTCDateTime


class MockInstance(IdMixin, TimestampMixin, Base):
    """A VM in the simulated cloud used by MOCK_MODE.

    This is "reality" for the fake cloud, deliberately separate from ``cluster_nodes`` (the
    control plane's belief), so failures injected here must be *detected* by the monitor.
    """

    __tablename__ = "mock_instances"
    __table_args__ = (UniqueConstraint("project_id", "zone", "name"),)

    cluster_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    project_id: Mapped[str] = mapped_column(String(100))
    zone: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(63))
    node_name: Mapped[str] = mapped_column(String(63))
    private_ip: Mapped[str] = mapped_column(String(45))
    status: Mapped[str] = mapped_column(String(20))
    engine_version: Mapped[str] = mapped_column(String(40))
    boot_started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # e.g. {"vm_down": true, "disk_pressure": true}
    faults: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
