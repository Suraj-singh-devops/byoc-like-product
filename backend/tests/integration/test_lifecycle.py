"""API -> database -> worker -> providers, end to end in mock mode (TRD §33 minimum E2E):
create, provision, health check, scale, health check, delete, audit."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import cluster_request, delete_cluster

RunWorker = Callable[[], list[str]]


def audit_trail(client: TestClient, headers: dict[str, str], resource_id: str) -> list[tuple[str, str]]:
    items = client.get(f"/api/v1/audit-logs?resource_id={resource_id}&limit=100", headers=headers).json()["items"]
    return [(e["action"], e["status"]) for e in reversed(items)]


def test_cluster_lifecycle(
    client: TestClient, owner: dict[str, str], network: dict[str, Any], run_worker: RunWorker
) -> None:
    # Create
    created = client.post("/api/v1/clusters", json=cluster_request(network), headers=owner).json()
    cluster_id, operation_id = created["cluster_id"], created["operation_id"]
    run_worker()
    operation = client.get(f"/api/v1/operations/{operation_id}", headers=owner).json()
    assert operation["status"] == "COMPLETED", operation["error"]
    assert [s["key"] for s in operation["steps"]] == [
        "validate",
        "validate_cloud",
        "plan",
        "apply",
        "bootstrap",
        "configure",
        "health",
        "register",
    ]
    assert all(s["status"] == "completed" for s in operation["steps"])
    assert operation["progress"] == 100 and operation["started_at"] and operation["completed_at"]
    assert any("Plan: " in entry["message"] for entry in operation["log"])

    cluster = client.get(f"/api/v1/clusters/{cluster_id}", headers=owner).json()
    assert (cluster["lifecycle"], cluster["health"]) == ("ACTIVE", "HEALTHY")
    assert cluster["observed_generation"] == cluster["generation"] == 1
    assert cluster["actual_state"]["engine"]["version"] == "9.5.4"
    assert cluster["actual_state"]["engine_settings"]["package"]["signing_key_fingerprint"] == (
        "46095ACC8548582C1A2699A9D27D666CD88E42B4"
    )
    assert cluster["actual_state"]["infrastructure"]["http_endpoints"]
    assert {n["zone"] for n in cluster["nodes"]} == {"asia-south1-a", "asia-south1-b", "asia-south1-c"}
    assert all(n["lifecycle"] == "ACTIVE" and n["health"] == "HEALTHY" for n in cluster["nodes"])
    assert cluster["metrics"]["cpu_percent"] is not None
    assert cluster["metrics"]["engine"]["jvm_heap_percent"] is not None

    # Scale 3 -> 5
    scale = client.post(f"/api/v1/clusters/{cluster_id}/scale", json={"node_count": 5}, headers=owner).json()
    run_worker()
    op = client.get(f"/api/v1/operations/{scale['operation_id']}", headers=owner).json()
    assert op["status"] == "COMPLETED", op["error"]
    assert [s["key"] for s in op["steps"]] == ["validate", "plan", "apply", "bootstrap", "configure", "health"]
    assert op["result"]["added"] == ["node-4", "node-5"]
    cluster = client.get(f"/api/v1/clusters/{cluster_id}", headers=owner).json()
    assert (cluster["lifecycle"], cluster["health"]) == ("ACTIVE", "HEALTHY")
    assert len(cluster["nodes"]) == 5 and cluster["actual_state"]["nodes"]["count"] == 5
    assert cluster["observed_generation"] == cluster["generation"] == 2
    assert [n["role"] for n in cluster["nodes"]][3:] == ["data,ingest", "data,ingest"]

    # Health check after scaling
    check = client.post(f"/api/v1/clusters/{cluster_id}/health-check", headers=owner).json()
    run_worker()
    result = client.get(f"/api/v1/operations/{check['id']}", headers=owner).json()["result"]
    assert result["state"] == "HEALTHY" and result["nodes_reporting"] == 5

    # Delete
    deleted = delete_cluster(client, owner, cluster).json()
    assert deleted["lifecycle"] == "DELETING"
    run_worker()
    assert client.get(f"/api/v1/operations/{deleted['operation_id']}", headers=owner).json()["status"] == "COMPLETED"
    assert client.get("/api/v1/clusters", headers=owner).json() == []
    history = client.get("/api/v1/clusters?include_deleted=true", headers=owner).json()
    assert history[0]["lifecycle"] == "DELETED"
    assert client.get(f"/api/v1/clusters/{cluster_id}/nodes", headers=owner).json() == []

    # Audit: every change is recorded as it starts and as it completes (TRD §35).
    assert audit_trail(client, owner, cluster_id) == [
        ("CLUSTER_CREATE_STARTED", "SUCCESS"),
        ("CLUSTER_CREATED", "SUCCESS"),
        ("CLUSTER_SCALE_STARTED", "SUCCESS"),
        ("CLUSTER_SCALE_COMPLETED", "SUCCESS"),
        ("CLUSTER_HEALTH_CHECK_REQUESTED", "SUCCESS"),
        ("CLUSTER_HEALTH_CHECK_COMPLETED", "SUCCESS"),
        ("CLUSTER_DELETE_STARTED", "SUCCESS"),
        ("CLUSTER_DELETE_COMPLETED", "SUCCESS"),
    ]
    entry = client.get("/api/v1/audit-logs?action=CLUSTER_SCALE_STARTED", headers=owner).json()["items"][0]
    assert entry["user"] == "owner@acme.example" and entry["organization"] == "Acme Corp"
    assert entry["resource"] == "production-search" and entry["resource_id"] == cluster_id
    assert entry["details"]["params"] == {"from": 3, "to": 5}

    # The name can be reused once the old cluster is gone.
    again = client.post("/api/v1/clusters", json=cluster_request(network), headers=owner)
    assert again.status_code == 202


def test_single_node_first_then_ha(
    client: TestClient, owner: dict[str, str], network: dict[str, Any], run_worker: RunWorker
) -> None:
    created = client.post(
        "/api/v1/clusters",
        json=cluster_request(network, name="dev-search", node_count=1, high_availability=False, storage_gb=50),
        headers=owner,
    ).json()
    run_worker()
    cluster = client.get(f"/api/v1/clusters/{created['cluster_id']}", headers=owner).json()
    assert (cluster["lifecycle"], cluster["health"]) == ("ACTIVE", "HEALTHY")
    assert cluster["metrics"]["engine"]["cluster_status"] == "yellow"
    assert "no redundancy" in cluster["health_details"]["warnings"][0]
    # Growing a single node into a 3-node cluster.
    client.post(f"/api/v1/clusters/{created['cluster_id']}/scale", json={"node_count": 3}, headers=owner)
    run_worker()
    cluster = client.get(f"/api/v1/clusters/{created['cluster_id']}", headers=owner).json()
    assert (cluster["lifecycle"], cluster["health"], len(cluster["nodes"])) == ("ACTIVE", "HEALTHY", 3)
    assert cluster["metrics"]["engine"]["cluster_status"] == "green"
