"""Failure handling, cancellation, retries, abandoned operations and the Redis plumbing."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.application.platform import Platform
from app.application.provisioning.runner import OperationRunner
from app.infrastructure.db import session_scope
from app.infrastructure.queue import RedisOperationQueue
from app.infrastructure.rate_limit import RedisRateLimiter
from app.models import Operation
from app.providers.cloud.mock.dataplane import MockDataPlane
from app.providers.cloud.mock.gcp import MockGcpProvider
from app.workers.monitor import RedisLeaderLock
from app.workers.reaper import OperationReaper
from tests.conftest import cluster_request, delete_cluster

RunWorker = Callable[[], list[str]]


def create(client: TestClient, headers: dict[str, str], network: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    response = client.post("/api/v1/clusters", json=cluster_request(network, **overrides), headers=headers)
    assert response.status_code == 202, response.text
    return response.json()


def test_bootstrap_failure_then_retry(
    client: TestClient,
    owner: dict[str, str],
    network: dict[str, Any],
    run_worker: RunWorker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = MockDataPlane.create_instance

    def broken(self: MockDataPlane, **kwargs: Any) -> Any:
        instance = original(self, **kwargs)
        if kwargs["node_name"] == "node-2":
            self.set_fault(kwargs["cluster_id"], "node-2", "bootstrap_fail")
        return instance

    monkeypatch.setattr(MockDataPlane, "create_instance", broken)
    created = create(client, owner, network)
    run_worker()
    op = client.get(f"/api/v1/operations/{created['operation_id']}", headers=owner).json()
    assert op["status"] == "FAILED"
    assert op["error_code"] == "BOOTSTRAP_FAILED"
    assert op["error"]["reason"].startswith("apt-get install elasticsearch failed")
    assert op["error"]["suggested_action"]
    assert [s["status"] for s in op["steps"]][:5] == ["completed", "completed", "completed", "completed", "failed"]
    cluster = client.get(f"/api/v1/clusters/{created['cluster_id']}", headers=owner).json()
    assert cluster["lifecycle"] == "FAILED" and "Bootstrap failed on node-2" in cluster["status_message"]
    audit = client.get("/api/v1/audit-logs?action=OPERATION_FAILED", headers=owner).json()["items"]
    assert audit[0]["status"] == "FAILURE" and audit[0]["details"]["error"]["code"] == "BOOTSTRAP_FAILED"
    assert audit[0]["details"]["operation_type"] == "CREATE_CLUSTER"

    # Fix the cause and retry: the workflow is idempotent and reuses the VMs already created.
    monkeypatch.setattr(MockDataPlane, "create_instance", original)
    client.post(
        f"/api/v1/mock/clusters/{created['cluster_id']}/faults",
        json={"node_name": "node-2", "fault": "clear"},
        headers=owner,
    )
    retry = client.post(f"/api/v1/operations/{created['operation_id']}/retry", headers=owner)
    assert retry.status_code == 202
    run_worker()
    assert client.get(f"/api/v1/operations/{retry.json()['id']}", headers=owner).json()["status"] == "COMPLETED"
    cluster = client.get(f"/api/v1/clusters/{created['cluster_id']}", headers=owner).json()
    assert (cluster["lifecycle"], len(cluster["nodes"])) == ("ACTIVE", 3)
    assert client.post(f"/api/v1/operations/{created['operation_id']}/retry", headers=owner).status_code == 409
    actions = [e["action"] for e in client.get("/api/v1/audit-logs?limit=100", headers=owner).json()["items"]]
    assert {"OPERATION_FAILED", "OPERATION_RETRIED", "CLUSTER_CREATED"} <= set(actions)


def test_cancel_pending_create(
    client: TestClient, owner: dict[str, str], network: dict[str, Any], run_worker: RunWorker
) -> None:
    created = create(client, owner, network)
    cancelled = client.post(f"/api/v1/operations/{created['operation_id']}/cancel", headers=owner).json()
    assert cancelled["status"] == "CANCELLED" and cancelled["error_message"] == "Cancelled by owner@acme.example"
    assert run_worker() == [created["operation_id"]]  # the queued message is ignored
    cluster = client.get(f"/api/v1/clusters/{created['cluster_id']}", headers=owner).json()
    assert cluster["lifecycle"] == "FAILED" and "cancelled" in cluster["status_message"]
    assert delete_cluster(client, owner, cluster).status_code == 202
    run_worker()
    assert client.get("/api/v1/clusters", headers=owner).json() == []


def test_cancel_running_operation_stops_at_next_step(
    client: TestClient,
    owner: dict[str, str],
    network: dict[str, Any],
    run_worker: RunWorker,
    platform: Platform,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = MockGcpProvider.plan_infrastructure

    def plan_then_user_cancels(self: MockGcpProvider, *args: Any, **kwargs: Any) -> Any:
        summary = original(self, *args, **kwargs)
        with session_scope(platform.session_factory) as s:
            s.query(Operation).filter(Operation.operation_type == "CREATE_CLUSTER").update({"cancel_requested": True})
        return summary

    monkeypatch.setattr(MockGcpProvider, "plan_infrastructure", plan_then_user_cancels)
    created = create(client, owner, network)
    run_worker()
    op = client.get(f"/api/v1/operations/{created['operation_id']}", headers=owner).json()
    assert op["status"] == "CANCELLED"
    assert {s["key"]: s["status"] for s in op["steps"]}["apply"] == "pending"


def test_permission_denied_during_provisioning_is_actionable(
    client: TestClient,
    owner: dict[str, str],
    network: dict[str, Any],
    run_worker: RunWorker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.cloud.gcp.errors import permission_denied

    def denied(self: MockGcpProvider, *args: Any, **kwargs: Any) -> Any:
        raise permission_denied("compute.instances.create", "acme-prod")

    monkeypatch.setattr(MockGcpProvider, "apply_infrastructure", denied)
    created = create(client, owner, network)
    run_worker()
    op = client.get(f"/api/v1/operations/{created['operation_id']}", headers=owner).json()
    assert op["error_code"] == "GCP_PERMISSION_DENIED"
    assert op["error"]["reason"] == "Service account does not have compute.instances.create permission."
    assert "Traceback" not in str(op)


def test_unexpected_crash_is_reported_without_internals(
    client: TestClient,
    owner: dict[str, str],
    network: dict[str, Any],
    run_worker: RunWorker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def crash(self: MockGcpProvider, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(MockGcpProvider, "plan_infrastructure", crash)
    created = create(client, owner, network)
    run_worker()
    op = client.get(f"/api/v1/operations/{created['operation_id']}", headers=owner).json()
    assert op["status"] == "FAILED" and op["error_code"] == "INTERNAL_ERROR"
    assert "secret internal detail" not in str(op)
    assert created["operation_id"] in op["error"]["reason"]


def test_reaper_requeues_abandoned_operations(
    client: TestClient, owner: dict[str, str], network: dict[str, Any], platform: Platform, run_worker: RunWorker
) -> None:
    created = create(client, owner, network)
    platform.queue.drain()  # type: ignore[attr-defined]
    op_id = uuid.UUID(created["operation_id"])
    assert OperationRunner(platform, "dead-worker").claim(op_id)
    with session_scope(platform.session_factory) as s:
        op = s.get(Operation, op_id)
        assert op is not None
        op.heartbeat_at = datetime.now(UTC) - timedelta(minutes=30)
    assert OperationReaper(platform).tick()["requeued"] == 1
    assert client.get(f"/api/v1/operations/{op_id}", headers=owner).json()["status"] == "PENDING"
    run_worker()
    op = client.get(f"/api/v1/operations/{op_id}", headers=owner).json()
    assert op["status"] == "COMPLETED" and op["attempt"] == 2
    assert any("re-queued" in e["message"] for e in op["log"])


def test_reaper_gives_up_after_max_attempts(
    client: TestClient, owner: dict[str, str], network: dict[str, Any], platform: Platform
) -> None:
    created = create(client, owner, network)
    op_id = uuid.UUID(created["operation_id"])
    with session_scope(platform.session_factory) as s:
        op = s.get(Operation, op_id)
        assert op is not None
        op.status, op.attempt, op.heartbeat_at = "PROVISIONING", 3, datetime.now(UTC) - timedelta(hours=1)
    assert OperationReaper(platform).tick()["failed"] == 1
    op_body = client.get(f"/api/v1/operations/{op_id}", headers=owner).json()
    assert op_body["status"] == "FAILED" and op_body["error_code"] == "OPERATION_ABANDONED"
    assert client.get(f"/api/v1/clusters/{created['cluster_id']}", headers=owner).json()["lifecycle"] == "FAILED"


def test_reaper_cancels_an_abandoned_preempted_operation_and_wakes_the_delete(
    client: TestClient,
    owner: dict[str, str],
    running_cluster: dict[str, Any],
    platform: Platform,
    run_worker: RunWorker,
) -> None:
    scale = client.post(f"/api/v1/clusters/{running_cluster['id']}/scale", json={"node_count": 5}, headers=owner).json()
    platform.queue.drain()  # type: ignore[attr-defined]
    assert OperationRunner(platform, "dead-worker").claim(uuid.UUID(scale["operation_id"]))
    deleted = delete_cluster(client, owner, running_cluster).json()
    platform.queue.drain()  # type: ignore[attr-defined]
    with session_scope(platform.session_factory) as s:
        op = s.get(Operation, uuid.UUID(scale["operation_id"]))
        assert op is not None
        op.heartbeat_at = datetime.now(UTC) - timedelta(minutes=30)  # its worker died mid-scale
    assert OperationReaper(platform).tick()["cancelled"] == 1
    assert platform.queue.drain() == [deleted["operation_id"]]  # type: ignore[attr-defined]
    platform.queue.enqueue(deleted["operation_id"])
    run_worker()
    assert client.get(f"/api/v1/operations/{deleted['operation_id']}", headers=owner).json()["status"] == "COMPLETED"


def test_reaper_requeues_lost_messages(
    client: TestClient, owner: dict[str, str], network: dict[str, Any], platform: Platform
) -> None:
    created = create(client, owner, network)
    platform.queue.drain()  # type: ignore[attr-defined]  # message lost
    with session_scope(platform.session_factory) as s:
        s.query(Operation).update({"last_enqueued_at": datetime.now(UTC) - timedelta(minutes=10)})
    OperationReaper(platform).tick()
    assert platform.queue.drain() == [created["operation_id"]]  # type: ignore[attr-defined]


def test_reaper_does_not_duplicate_waiting_messages(
    client: TestClient, owner: dict[str, str], network: dict[str, Any], platform: Platform
) -> None:
    created = create(client, owner, network)
    with session_scope(platform.session_factory) as s:
        s.query(Operation).update({"last_enqueued_at": datetime.now(UTC) - timedelta(minutes=10)})
    assert OperationReaper(platform).tick()["requeued"] == 0  # the message is still queued
    assert platform.queue.drain() == [created["operation_id"]]  # type: ignore[attr-defined]


def test_duplicate_queue_messages_are_harmless(
    client: TestClient, owner: dict[str, str], network: dict[str, Any], platform: Platform, run_worker: RunWorker
) -> None:
    created = create(client, owner, network)
    platform.queue.enqueue(created["operation_id"])
    platform.queue.enqueue(created["operation_id"])
    run_worker()
    op = client.get(f"/api/v1/operations/{created['operation_id']}", headers=owner).json()
    assert op["status"] == "COMPLETED" and op["attempt"] == 1


class TestRedisPlumbing:
    def test_queue(self) -> None:
        queue = RedisOperationQueue(fakeredis.FakeRedis())
        queue.enqueue("a")
        queue.enqueue("b")
        assert queue.depth() == 2
        assert queue.contains("a") and not queue.contains("c")
        assert [queue.dequeue(1), queue.dequeue(1)] == ["a", "b"]
        assert not queue.contains("a")

    def test_rate_limiter(self) -> None:
        limiter = RedisRateLimiter(fakeredis.FakeRedis())
        assert all(limiter.hit("k", 3, 60) for _ in range(3))
        assert not limiter.hit("k", 3, 60)
        limiter.reset("k")
        assert limiter.hit("k", 3, 60)

    def test_leader_lock(self) -> None:
        client = fakeredis.FakeRedis()
        first = RedisLeaderLock(client, "lock", "worker-1", 30)
        second = RedisLeaderLock(client, "lock", "worker-2", 30)
        assert first.acquire() and first.acquire()
        assert not second.acquire()
