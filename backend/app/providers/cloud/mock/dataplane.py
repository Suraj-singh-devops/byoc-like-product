"""Simulated data plane for MOCK_MODE: VMs, the bootstrap script, Elasticsearch and agents.

Behaviour is a deterministic function of time since boot plus injected faults, so no
background process is needed and every control-plane code path (bootstrap waits, health
evaluation, failure detection, approved agent actions) runs exactly as it would for real.
"""

from __future__ import annotations

import math
import random
import uuid
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select

from app.config.settings import Settings
from app.domain.enums import AgentCommandStatus, BootstrapStatus
from app.infrastructure.db import SessionFactory, session_scope
from app.models import AgentCommand, ClusterNode, MockInstance
from app.providers.cloud.base import GuestReport, NodeRef

FAULTS = ("vm_down", "agent_down", "es_down", "disk_pressure", "heap_pressure", "cpu_spike", "bootstrap_fail")

# (seconds after boot at speed 1.0, status, message)
BOOT_TIMELINE: tuple[tuple[float, BootstrapStatus, str], ...] = (
    (0.0, BootstrapStatus.INSTALLING, "Formatting data disk and installing Elasticsearch {version}"),
    (6.0, BootstrapStatus.CONFIGURING, "Writing elasticsearch.yml, TLS certificates and keystore"),
    (10.0, BootstrapStatus.STARTING, "Starting Elasticsearch and the BYOC agent"),
    (13.0, BootstrapStatus.READY, "Elasticsearch {version} running; agent reporting"),
)
SHARDS_PER_NODE = 8


def utcnow() -> datetime:
    return datetime.now(UTC)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _ordinal(node_name: str) -> int:
    try:
        return int(node_name.rsplit("-", 1)[-1])
    except ValueError:
        return 99


class MockDataPlane:
    def __init__(self, settings: Settings, session_factory: SessionFactory) -> None:
        self.settings = settings
        self.session_factory = session_factory

    @property
    def speed(self) -> float:
        return self.settings.mock_speed

    def _elapsed(self, instance: MockInstance, now: datetime) -> float:
        """Seconds since boot, normalised to speed 1.0 (speed 0 means instantly ready)."""
        if self.speed <= 0:
            return 1e9
        return (now - instance.boot_started_at).total_seconds() / self.speed

    # --------------------------------------------------------------- inventory

    def instances(self, cluster_id: str) -> list[MockInstance]:
        with session_scope(self.session_factory) as s:
            rows = s.scalars(select(MockInstance).where(MockInstance.cluster_id == uuid.UUID(cluster_id))).all()
            return sorted(rows, key=lambda r: _ordinal(r.node_name))

    def create_instance(
        self,
        *,
        cluster_id: str,
        project_id: str,
        zone: str,
        name: str,
        node_name: str,
        version: str,
        labels: dict[str, Any],
        private_ip: str | None = None,
    ) -> MockInstance:
        """``private_ip`` comes from the cluster's subnet; clusters created before registered networks
        use their dedicated subnet's 10.10.0.0/24."""
        with session_scope(self.session_factory) as s:
            existing = s.scalar(
                select(MockInstance).where(
                    MockInstance.project_id == project_id, MockInstance.zone == zone, MockInstance.name == name
                )
            )
            if existing is not None:
                return existing  # idempotent: re-applying does not recreate the VM
            instance = MockInstance(
                cluster_id=uuid.UUID(cluster_id),
                project_id=project_id,
                zone=zone,
                name=name,
                node_name=node_name,
                private_ip=private_ip or f"10.10.0.{10 + _ordinal(node_name)}",
                status="RUNNING",
                engine_version=version,
                boot_started_at=utcnow(),
                faults={},
                labels=labels,
            )
            s.add(instance)
            s.flush()
            return instance

    def update_config(self, cluster_id: str, node_name: str, config: dict[str, Any]) -> None:
        """What the agent does with new metadata (docs/adr/0017): live settings take effect at once
        (the elected master applies them); a new config generation restarts Elasticsearch."""
        with session_scope(self.session_factory) as s:
            instance = s.scalar(
                select(MockInstance).where(
                    MockInstance.cluster_id == uuid.UUID(cluster_id), MockInstance.node_name == node_name
                )
            )
            if instance is None:
                return
            labels = dict(instance.labels or {})
            restart = labels.get("config_generation", 0) != config.get("config_generation", 0)
            # Simulated Elasticsearch refuses settings whose name starts with "unknown." at startup,
            # like a real node refuses an unknown setting; apply-config restores the previous file
            # (docs/adr/0018, docs/mock-mode.md).
            unknown = sorted(k for k in config.get("node_settings_keys") or [] if k.startswith("unknown."))
            if restart and unknown:
                if labels.get("rejected_generation") == config.get("config_generation"):
                    return  # the agent never retries a rejected generation
                labels["rejected_generation"] = config.get("config_generation")
                labels["rejected_reason"] = (
                    f"java.lang.IllegalArgumentException: unknown setting [{unknown[0]}] please check that any "
                    "required plugins are installed, or check the breaking changes documentation for removed settings"
                )
                labels["cluster_settings_hash"] = config.get("cluster_settings_hash")
                instance.labels = labels
                starting_at = BOOT_TIMELINE[2][0]
                instance.boot_started_at = utcnow() - timedelta(seconds=starting_at * self.speed)
                return
            labels.update(config)
            if restart:
                labels.pop("rejected_generation", None)
                labels.pop("rejected_reason", None)
            instance.labels = labels
            if restart:
                starting_at = BOOT_TIMELINE[2][0]
                instance.boot_started_at = utcnow() - timedelta(seconds=starting_at * self.speed)

    def delete_instance(self, project_id: str, zone: str, name: str) -> None:
        with session_scope(self.session_factory) as s:
            s.execute(
                delete(MockInstance).where(
                    MockInstance.project_id == project_id, MockInstance.zone == zone, MockInstance.name == name
                )
            )

    def delete_cluster(self, cluster_id: str) -> None:
        with session_scope(self.session_factory) as s:
            s.execute(delete(MockInstance).where(MockInstance.cluster_id == uuid.UUID(cluster_id)))

    def set_fault(self, cluster_id: str, node_name: str, fault: str) -> MockInstance | None:
        with session_scope(self.session_factory) as s:
            instance = s.scalar(
                select(MockInstance).where(
                    MockInstance.cluster_id == uuid.UUID(cluster_id), MockInstance.node_name == node_name
                )
            )
            if instance is None:
                return None
            faults = dict(instance.faults or {})
            was_down = instance.status != "RUNNING"
            if fault == "clear":
                faults = {}
                instance.status = "RUNNING"
                if was_down:
                    # The VM restarts: software is already installed, so it goes straight to
                    # "starting" and rejoins the cluster a few seconds later.
                    starting_at = BOOT_TIMELINE[2][0]
                    instance.boot_started_at = utcnow() - timedelta(seconds=starting_at * self.speed)
            else:
                faults[fault] = True
                if fault == "vm_down":
                    instance.status = "TERMINATED"
            instance.faults = faults
            return instance

    def statuses(self, project_id: str, nodes: list[NodeRef]) -> dict[str, str]:
        with session_scope(self.session_factory) as s:
            rows = {
                (r.zone, r.name): r
                for r in s.scalars(select(MockInstance).where(MockInstance.project_id == project_id)).all()
            }
        result = {}
        for node in nodes:
            row = rows.get((node.zone, node.instance_name))
            result[node.name] = "NOT_FOUND" if row is None else row.status
        return result

    # ------------------------------------------------------------ simulation

    def _bootstrap(self, instance: MockInstance, now: datetime) -> dict[str, Any]:
        if (instance.faults or {}).get("bootstrap_fail"):
            return {
                "status": BootstrapStatus.FAILED.value,
                "message": "apt-get install elasticsearch failed: unable to reach artifacts.elastic.co",
                "updated_at": now.isoformat(),
            }
        elapsed = self._elapsed(instance, now)
        stage = BOOT_TIMELINE[0]
        for entry in BOOT_TIMELINE:
            jitter = 0.4 * (_ordinal(instance.node_name) - 1)
            if elapsed >= entry[0] + jitter:
                stage = entry
        return {
            "status": stage[1].value,
            "message": stage[2].format(version=instance.engine_version),
            "updated_at": now.isoformat(),
        }

    def _process_commands(self, cluster_id: uuid.UUID, instances: list[MockInstance], now: datetime) -> None:
        """Simulated agents: pick up approved commands like a heartbeat would."""
        live = {
            i.node_name
            for i in instances
            if i.status == "RUNNING" and not {"vm_down", "agent_down"} & set(i.faults or {})
        }
        with session_scope(self.session_factory) as s:
            pending = s.execute(
                select(AgentCommand, ClusterNode.name)
                .join(ClusterNode, ClusterNode.id == AgentCommand.node_id)
                .where(
                    AgentCommand.cluster_id == cluster_id,
                    AgentCommand.status.in_([AgentCommandStatus.PENDING, AgentCommandStatus.SENT]),
                )
            ).all()
            if not pending:
                return
            by_name = {
                i.node_name: i for i in s.scalars(select(MockInstance).where(MockInstance.cluster_id == cluster_id))
            }
            for command, node_name in pending:
                if node_name not in live:
                    continue
                if command.command == "restart_engine":
                    # Elasticsearch restarts: the node goes back to "starting" and rejoins shortly.
                    target = by_name.get(node_name)
                    if target is not None:
                        starting_at = BOOT_TIMELINE[2][0]
                        target.boot_started_at = now - timedelta(seconds=starting_at * self.speed)
                command.status = AgentCommandStatus.SUCCEEDED
                command.sent_at = command.sent_at or now
                command.completed_at = now
                command.result = {"simulated": True}
                command.message = f"{command.command} completed on {node_name} (simulated)"

    def reports(self, project_id: str, nodes: list[NodeRef]) -> dict[str, GuestReport]:
        now = utcnow()
        with session_scope(self.session_factory) as s:
            wanted = {(n.zone, n.instance_name): n for n in nodes}
            rows = [
                r
                for r in s.scalars(select(MockInstance).where(MockInstance.project_id == project_id)).all()
                if (r.zone, r.name) in wanted
            ]
            cluster_ids = {r.cluster_id for r in rows}
            peers = {
                cid: s.scalars(select(MockInstance).where(MockInstance.cluster_id == cid)).all() for cid in cluster_ids
            }
        for cid, members in peers.items():
            self._process_commands(cid, list(members), now)
        if cluster_ids:
            with session_scope(self.session_factory) as s:
                peers = {
                    cid: s.scalars(select(MockInstance).where(MockInstance.cluster_id == cid)).all()
                    for cid in cluster_ids
                }

        result: dict[str, GuestReport] = {n.name: GuestReport(node_name=n.name) for n in nodes}
        for row in rows:
            node = wanted[(row.zone, row.name)]
            faults = row.faults or {}
            if row.status != "RUNNING" or faults.get("vm_down"):
                continue
            bootstrap = self._bootstrap(row, now)
            report = None
            if bootstrap["status"] == BootstrapStatus.READY and not faults.get("agent_down"):
                report = self._agent_report(row, list(peers[row.cluster_id]), now)
            result[node.name] = GuestReport(node_name=node.name, bootstrap=bootstrap, report=report)
        return result

    def _es_up(self, instance: MockInstance, now: datetime) -> bool:
        faults = instance.faults or {}
        return (
            instance.status == "RUNNING"
            and not faults.get("vm_down")
            and not faults.get("es_down")
            and self._bootstrap(instance, now)["status"] == BootstrapStatus.READY
        )

    def _agent_report(self, instance: MockInstance, peers: list[MockInstance], now: datetime) -> dict[str, Any]:
        faults = instance.faults or {}
        seed = zlib.crc32(f"{instance.cluster_id}:{instance.node_name}".encode())
        phase = (seed % 1000) / 1000 * 2 * math.pi
        rnd = random.Random(seed + int(now.timestamp() // 5))
        t = now.timestamp()
        hours_up = max(0.0, (now - instance.boot_started_at).total_seconds() / 3600)

        cpu = _clamp(27 + 13 * math.sin(t / 97 + phase) + 5 * math.sin(t / 13 + 2 * phase) + rnd.uniform(-3, 3), 2, 99)
        memory = _clamp(58 + 6 * math.sin(t / 211 + phase) + rnd.uniform(-2, 2), 10, 99)
        heap = _clamp(47 + 13 * math.sin(t / 53 + phase) + rnd.uniform(-4, 4), 5, 84)
        disk = _clamp(14 + (seed % 17) + hours_up * 0.4, 1, 78)
        if faults.get("cpu_spike"):
            cpu = 97.0
        if faults.get("heap_pressure"):
            heap = 94.0
        if faults.get("disk_pressure"):
            disk = 93.5
        storage_gb = int((instance.labels or {}).get("storage_gb", 100))
        disk_total = storage_gb * 1024**3
        memory_total = int((instance.labels or {}).get("memory_gb", 16)) * 1024**3

        system = {
            "cpu_percent": round(cpu, 1),
            "memory_percent": round(memory, 1),
            "memory_total_bytes": memory_total,
            "memory_used_bytes": int(memory_total * memory / 100),
            "disk_percent": round(disk, 1),
            "disk_total_bytes": disk_total,
            "disk_used_bytes": int(disk_total * disk / 100),
            "disk_read_bytes_per_sec": round(max(0.0, 2.5e6 + 1.5e6 * math.sin(t / 41 + phase)), 1),
            "disk_write_bytes_per_sec": round(max(0.0, 4.0e6 + 2.0e6 * math.sin(t / 37 + phase)), 1),
            "network_rx_bytes_per_sec": round(max(0.0, 1.2e6 + 8e5 * math.sin(t / 29 + phase)), 1),
            "network_tx_bytes_per_sec": round(max(0.0, 9e5 + 6e5 * math.sin(t / 31 + phase)), 1),
            "load1": round(cpu / 25, 2),
            "uptime_seconds": round(hours_up * 3600, 0),
        }

        engine: dict[str, Any] = {"type": "elasticsearch", "version": instance.engine_version}
        if faults.get("es_down"):
            engine.update({"reachable": False, "error": "connection refused (elasticsearch.service is not running)"})
        else:
            engine.update(self._cluster_view(instance, peers, now, t, phase, rnd))
            engine["jvm_heap_percent"] = round(heap, 1)
            heap_max = memory_total // 2
            engine["jvm_heap_max_bytes"] = heap_max
            engine["jvm_heap_used_bytes"] = int(heap_max * heap / 100)
        return {
            "schema_version": 1,
            "agent_version": self.settings.agent_version,
            "node_name": instance.node_name,
            "hostname": (instance.labels or {}).get("hostname")
            or f"{instance.name}.{instance.zone}.c.{instance.project_id}.internal",
            "collected_at": now.isoformat(),
            "bootstrap": self._bootstrap(instance, now),
            "system": system,
            "engine": engine,
            "config": self._config_report(instance, engine),
        }

    @staticmethod
    def _config_report(instance: MockInstance, engine: dict[str, Any]) -> dict[str, Any]:
        """The managed settings the node reads back from Elasticsearch, and its applied generation."""
        labels = instance.labels or {}
        if not engine.get("reachable") or engine.get("cluster_status") is None:
            return {}
        report: dict[str, Any] = {
            "cluster_settings_hash": labels.get("cluster_settings_hash"),
            "generation": int(labels.get("config_generation", 0)),
        }
        if labels.get("rejected_generation") is not None:
            report["rejected_generation"] = int(labels["rejected_generation"])
            report["rejected_reason"] = labels.get("rejected_reason")
        return report

    def _cluster_view(
        self,
        instance: MockInstance,
        peers: list[MockInstance],
        now: datetime,
        t: float,
        phase: float,
        rnd: random.Random,
    ) -> dict[str, Any]:
        up = [p for p in peers if self._es_up(p, now)]
        masters = [p for p in peers if "master" in self._roles(p)]
        masters_up = [p for p in masters if p in up]
        data = [p for p in peers if "data" in self._roles(p)]
        data_up = [p for p in data if p in up]
        total = len(data)
        name = str((instance.labels or {}).get("cluster_name", "cluster"))
        view: dict[str, Any] = {"reachable": True, "cluster_name": name, "node_roles": self._roles(instance)}
        if len(masters_up) * 2 <= len(masters):
            view.update(
                {
                    "cluster_status": None,
                    "error": "master_not_discovered_exception: no master-eligible quorum",
                    "is_master": False,
                }
            )
            return view

        # Only data nodes hold shards (coordinating and dedicated master nodes hold none).
        allocation = {p.node_name: SHARDS_PER_NODE for p in data_up}
        primaries = SHARDS_PER_NODE * total // 2
        unassigned = (
            0 if len(data_up) == total and total > 1 else max(0, primaries - SHARDS_PER_NODE * (len(data_up) - 1))
        )
        if total == 1:
            unassigned = primaries  # replicas of a single data node can never be assigned
        if not data_up:
            unassigned = SHARDS_PER_NODE * total
        status = "green" if unassigned == 0 else ("red" if not data_up else "yellow")
        elected = min(masters_up, key=lambda p: int((p.labels or {}).get("ordinal", _ordinal(p.node_name))))
        docs = int(1_250_000 + (zlib.crc32(name.encode()) % 500_000) + (t % 86400) * 3)
        view.update(
            {
                "cluster_status": status,
                "number_of_nodes": len(up),
                "number_of_data_nodes": len(data_up),
                "active_shards": sum(allocation.values()),
                "unassigned_shards": unassigned,
                "relocating_shards": 0,
                "active_shards_percent": round(
                    100 * sum(allocation.values()) / max(1, sum(allocation.values()) + unassigned), 1
                ),
                "is_master": elected.node_name == instance.node_name,
                "shards": allocation.get(instance.node_name, 0),
                "allocation": allocation,
                "docs_count": docs,
                "search_rate": round(max(0.0, 140 + 60 * math.sin(t / 71 + phase) + rnd.uniform(-10, 10)), 1),
                "indexing_rate": round(max(0.0, 380 + 120 * math.sin(t / 89 + phase) + rnd.uniform(-20, 20)), 1),
                "error": None,
            }
        )
        return view

    @staticmethod
    def _roles(instance: MockInstance) -> list[str]:
        roles = (instance.labels or {}).get("roles")
        if roles is not None:
            return list(roles)
        return ["master", "data", "ingest"] if _ordinal(instance.node_name) <= 3 else ["data", "ingest"]
