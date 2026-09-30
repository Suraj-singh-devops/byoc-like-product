"""Schema of the report a node agent publishes (via heartbeat or guest attributes).

The platform understands ``system`` and ``bootstrap``. ``engine`` is opaque to the core and
interpreted by the database provider for the cluster's engine.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

REPORT_SCHEMA_VERSION = 1


class BootstrapInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    message: str | None = None
    updated_at: datetime | None = None


class SystemMetrics(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cpu_percent: float | None = None
    memory_percent: float | None = None
    memory_total_bytes: int | None = None
    memory_used_bytes: int | None = None
    disk_percent: float | None = None
    disk_total_bytes: int | None = None
    disk_used_bytes: int | None = None
    disk_read_bytes_per_sec: float | None = None
    disk_write_bytes_per_sec: float | None = None
    network_rx_bytes_per_sec: float | None = None
    network_tx_bytes_per_sec: float | None = None
    load1: float | None = None
    uptime_seconds: float | None = None


class NodeReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = REPORT_SCHEMA_VERSION
    agent_version: str | None = None
    node_name: str = Field(min_length=1, max_length=63)
    hostname: str | None = None
    collected_at: datetime | None = None
    bootstrap: BootstrapInfo | None = None
    system: SystemMetrics = Field(default_factory=SystemMetrics)
    engine: dict[str, Any] = Field(default_factory=dict)
