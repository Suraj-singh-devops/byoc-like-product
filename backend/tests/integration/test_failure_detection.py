"""Failure detection (TRD §25, §34): injected faults in the simulated data plane must be
detected by the monitor, shown as health changes and recorded as events."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.platform import Platform
from app.infrastructure.db import session_scope
from app.models import ClusterNode
from app.workers.monitor import HealthMonitor


def fault(client: TestClient, headers: dict[str, str], cluster_id: str, node: str, kind: str) -> None:
    response = client.post(
        f"/api/v1/mock/clusters/{cluster_id}/faults", json={"node_name": node, "fault": kind}, headers=headers
    )
    assert response.status_code == 200, response.text


def state(
    client: TestClient, headers: dict[str, str], cluster_id: str
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    cluster = client.get(f"/api/v1/clusters/{cluster_id}", headers=headers).json()
    events = client.get(f"/api/v1/clusters/{cluster_id}/events", headers=headers).json()
    return cluster, {n["name"]: n for n in cluster["nodes"]}, events


def age_reports(platform: Platform, cluster_id: str, node: str, seconds: int) -> None:
    with session_scope(platform.session_factory) as s:
        row = s.query(ClusterNode).filter_by(cluster_id=uuid.UUID(cluster_id), name=node, deleted_at=None).one()
        row.last_report_at = datetime.now(UTC) - timedelta(seconds=seconds)


@pytest.fixture
def monitor(cloud_platform: Platform) -> HealthMonitor:
    """The monitoring-worker's monitor: it has cloud access."""
    return HealthMonitor(cloud_platform)


def test_vm_down_then_recovery(
    client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any], monitor: HealthMonitor
) -> None:
    cluster_id = running_cluster["id"]
    fault(client, owner, cluster_id, "node-2", "vm_down")
    monitor.tick()
    cluster, nodes, events = state(client, owner, cluster_id)
    assert (cluster["lifecycle"], cluster["health"]) == ("ACTIVE", "DEGRADED"), "health never changes lifecycle"
    node2 = nodes["node-2"]
    assert (node2["lifecycle"], node2["health"], node2["instance_status"]) == ("ACTIVE", "UNHEALTHY", "TERMINATED")
    assert node2["infrastructure_health"] == "UNHEALTHY"
    assert any(e["event_type"] == "NODE_UNHEALTHY" and e["node_name"] == "node-2" for e in events)
    assert any(e["event_type"] == "CLUSTER_HEALTH_CHANGED" and "HEALTHY to DEGRADED" in e["message"] for e in events)

    # No duplicate events while nothing changes.
    count = len(events)
    monitor.tick()
    assert len(state(client, owner, cluster_id)[2]) == count

    fault(client, owner, cluster_id, "node-2", "clear")
    monitor.tick()
    cluster, nodes, events = state(client, owner, cluster_id)
    assert cluster["health"] == "HEALTHY" and nodes["node-2"]["health"] == "HEALTHY"
    assert any(e["event_type"] == "NODE_RECOVERED" for e in events)


def test_majority_down_is_unhealthy(
    client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any], monitor: HealthMonitor
) -> None:
    cluster_id = running_cluster["id"]
    fault(client, owner, cluster_id, "node-2", "vm_down")
    fault(client, owner, cluster_id, "node-3", "vm_down")
    monitor.tick()
    cluster = state(client, owner, cluster_id)[0]
    assert cluster["health"] == "UNHEALTHY"
    assert (cluster["health_details"]["infrastructure"], cluster["health_details"]["engine"]) == (
        "UNHEALTHY",
        "UNHEALTHY",
    )


def test_agent_unavailable_is_not_a_dead_vm(
    client: TestClient,
    owner: dict[str, str],
    running_cluster: dict[str, Any],
    monitor: HealthMonitor,
    platform: Platform,
) -> None:
    cluster_id = running_cluster["id"]
    fault(client, owner, cluster_id, "node-3", "agent_down")
    age_reports(platform, cluster_id, "node-3", 300)
    monitor.tick()
    cluster, nodes, events = state(client, owner, cluster_id)
    assert cluster["health"] == "DEGRADED"
    node3 = nodes["node-3"]
    assert (node3["health"], node3["agent_status"], node3["instance_status"]) == ("UNKNOWN", "STALE", "RUNNING")
    assert node3["infrastructure_health"] == "HEALTHY", "a running VM is not assumed dead (TRD §34)"
    assert any(e["event_type"] == "NODE_STATUS_UNKNOWN" and "Agent unavailable" in e["message"] for e in events)


def test_elasticsearch_down_on_a_healthy_vm(
    client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any], monitor: HealthMonitor
) -> None:
    cluster_id = running_cluster["id"]
    fault(client, owner, cluster_id, "node-1", "es_down")
    monitor.tick()
    cluster, nodes, events = state(client, owner, cluster_id)
    node1 = nodes["node-1"]
    assert node1["health"] == "UNHEALTHY"
    assert (node1["infrastructure_health"], node1["engine_health"]) == ("HEALTHY", "UNHEALTHY")
    assert cluster["health"] == "DEGRADED"
    assert (cluster["health_details"]["infrastructure"], cluster["health_details"]["engine"]) == (
        "HEALTHY",
        "DEGRADED",
    )
    assert any("Elasticsearch unavailable" in e["message"] for e in events)


def test_disk_critical(
    client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any], monitor: HealthMonitor
) -> None:
    cluster_id = running_cluster["id"]
    fault(client, owner, cluster_id, "node-2", "disk_pressure")
    monitor.tick()
    _, nodes, events = state(client, owner, cluster_id)
    assert nodes["node-2"]["health"] == "UNHEALTHY"
    assert any("Disk usage critical" in e["message"] for e in events)


def test_warnings_do_not_change_health(
    client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any], monitor: HealthMonitor
) -> None:
    cluster_id = running_cluster["id"]
    fault(client, owner, cluster_id, "node-1", "cpu_spike")
    monitor.tick()
    cluster, nodes, _ = state(client, owner, cluster_id)
    assert cluster["health"] == "HEALTHY" and nodes["node-1"]["health"] == "HEALTHY"
    assert nodes["node-1"]["health_warnings"] == ["CPU usage high: 97%"]
    assert "node-1: CPU usage high: 97%" in cluster["health_details"]["warnings"]


def test_node_missing_from_cloud(
    client: TestClient,
    owner: dict[str, str],
    running_cluster: dict[str, Any],
    monitor: HealthMonitor,
    cloud_platform: Platform,
) -> None:
    from app.providers.cloud.mock.gcp import MockGcpProvider

    cloud = cloud_platform.registry.cloud("gcp")
    assert isinstance(cloud, MockGcpProvider)
    node = next(n for n in running_cluster["nodes"] if n["name"] == "node-3")
    cloud.dataplane.delete_instance("acme-prod", node["zone"], node["instance_name"])
    monitor.tick()
    nodes = {n["name"]: n for n in client.get(f"/api/v1/clusters/{running_cluster['id']}/nodes", headers=owner).json()}
    assert (nodes["node-3"]["lifecycle"], nodes["node-3"]["instance_status"]) == ("ACTIVE", "NOT_FOUND")
    assert "VM missing" in nodes["node-3"]["health_reasons"][0]


def test_on_demand_health_check_operation(
    client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any], run_worker: Any
) -> None:
    cluster_id = running_cluster["id"]
    fault(client, owner, cluster_id, "node-2", "heap_pressure")
    op = client.post(f"/api/v1/clusters/{cluster_id}/health-check", headers=owner)
    assert op.status_code == 202
    run_worker()
    result = client.get(f"/api/v1/operations/{op.json()['id']}", headers=owner).json()
    assert result["status"] == "COMPLETED"
    assert result["result"]["state"] == "DEGRADED"
    assert any("JVM heap critical" in r for n in result["result"]["nodes"] for r in n["reasons"])
