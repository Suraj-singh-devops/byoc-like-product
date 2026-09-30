from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.platform import Platform
from app.application.provisioning.outcome import finalize_operation
from app.application.provisioning.runner import OperationRunner, wake_waiting
from app.domain.states import OperationStatus
from app.infrastructure.db import session_scope
from app.models import Operation
from app.providers.cloud.base import CredentialValidationResult
from app.providers.cloud.mock.dataplane import MockDataPlane
from app.providers.cloud.mock.gcp import MockGcpProvider
from tests.conftest import account_request, cluster_request, delete_cluster, register_network

RunWorker = Callable[[], list[str]]


def audit_actions(client: TestClient, headers: dict[str, str], resource_id: str) -> list[str]:
    items = client.get(f"/api/v1/audit-logs?resource_id={resource_id}&limit=100", headers=headers).json()["items"]
    return [e["action"] for e in reversed(items)]


class TestCloudAccounts:
    def test_keyless_account_is_connected(
        self, client: TestClient, owner: dict[str, str], cloud_account: dict[str, Any]
    ) -> None:
        body = client.get(f"/api/v1/cloud-accounts/{cloud_account['id']}", headers=owner).json()
        assert body["status"] == "CONNECTED" and body["last_connected_at"]
        assert body["auth_type"] == "impersonation" and body["region"] == "asia-south1"
        assert body["service_account_email"] == "db-platform-provisioner@acme-prod.iam.gserviceaccount.com"
        assert not {"credentials_fingerprint", "state_bucket", "encrypted_credentials"} & set(body)

    def test_service_account_keys_are_rejected(self, client: TestClient, owner: dict[str, str]) -> None:
        response = client.post(
            "/api/v1/cloud-accounts",
            json={**account_request(), "service_account_key": '{"type": "service_account"}'},
            headers=owner,
        )
        assert response.status_code == 422
        assert "not accepted" in str(response.json()["error"]["details"])

    def test_service_account_must_belong_to_the_project(self, client: TestClient, owner: dict[str, str]) -> None:
        response = client.post(
            "/api/v1/cloud-accounts",
            json=account_request(service_account_email="provisioner@someone-else.iam.gserviceaccount.com"),
            headers=owner,
        )
        assert response.status_code == 422
        assert "service_account_email" in response.json()["error"]["details"]["fields"]

    def test_region_is_validated(self, client: TestClient, owner: dict[str, str]) -> None:
        response = client.post("/api/v1/cloud-accounts", json=account_request(region="mars-north1"), headers=owner)
        assert response.status_code == 422
        assert "region" in response.json()["error"]["details"]["fields"]

    def test_missing_permissions_are_explained(self, client: TestClient, owner: dict[str, str]) -> None:
        response = client.post("/api/v1/cloud-accounts", json=account_request("acme-denied"), headers=owner)
        assert response.status_code == 201
        account = response.json()
        assert account["status"] == "FAILED" and account["last_connected_at"] is None
        error = account["validation"]["error"]
        assert error["code"] == "GCP_PERMISSION_DENIED"
        assert error["reason"] == "Service account does not have compute.instances.create permission."
        assert "roles/compute.instanceAdmin.v1" in error["suggested_action"]

    def test_revoked_access_is_disconnected(
        self,
        client: TestClient,
        owner: dict[str, str],
        cloud_account: dict[str, Any],
        network: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def revoked(self: MockGcpProvider, account: Any) -> CredentialValidationResult:
            return CredentialValidationResult(valid=False, checks=[], error={"code": "GCP_PERMISSION_DENIED"})

        monkeypatch.setattr(MockGcpProvider, "validate_credentials", revoked)
        account = client.post(f"/api/v1/cloud-accounts/{cloud_account['id']}/validate", headers=owner).json()
        assert account["status"] == "DISCONNECTED"
        response = client.post("/api/v1/clusters", json=cluster_request(network), headers=owner)
        assert response.status_code == 422 and "network_id" in response.json()["error"]["details"]["fields"]
        assert "is not connected" in response.json()["error"]["message"]
        validated = client.get("/api/v1/audit-logs?action=CLOUD_ACCOUNT_VALIDATED", headers=owner).json()["items"]
        assert [e["status"] for e in validated] == ["FAILURE", "SUCCESS"]

    def test_catalog(self, client: TestClient, owner: dict[str, str], cloud_account: dict[str, Any]) -> None:
        catalog = client.get(f"/api/v1/cloud-accounts/{cloud_account['id']}/catalog", headers=owner).json()
        assert catalog["simulated"] is True
        assert any(r["name"] == "asia-south1" for r in catalog["regions"])
        machines = client.get(
            f"/api/v1/cloud-accounts/{cloud_account['id']}/machine-types?zone=asia-south1-a", headers=owner
        ).json()
        assert any(m["name"] == "e2-standard-8" and m["memory_gb"] == 32 for m in machines)

    def test_cannot_delete_account_in_use(
        self, client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any]
    ) -> None:
        response = client.delete(f"/api/v1/cloud-accounts/{running_cluster['cloud_account_id']}", headers=owner)
        assert response.status_code == 409 and "1 cluster" in response.json()["error"]["message"]


class TestCreateCluster:
    def test_accepted_response(self, client: TestClient, owner: dict[str, str], network: dict[str, Any]) -> None:
        response = client.post(
            "/api/v1/clusters", json=cluster_request(network, engine="elastic search"), headers=owner
        )
        assert response.status_code == 202
        body = response.json()
        assert set(body) == {"cluster_id", "operation_id", "lifecycle"}
        assert body["lifecycle"] == "CREATING"
        operation = client.get(f"/api/v1/operations/{body['operation_id']}", headers=owner).json()
        assert operation["status"] == "PENDING" and operation["operation_type"] == "CREATE_CLUSTER"
        cluster = client.get(f"/api/v1/clusters/{body['cluster_id']}", headers=owner).json()
        assert (cluster["lifecycle"], cluster["health"]) == ("CREATING", "UNKNOWN")
        assert cluster["engine"] == "elasticsearch" and cluster["engine_version"] == "9.5.4"
        assert cluster["project_id"] == "acme-prod"
        assert cluster["desired_state"]["nodes"]["count"] == 3
        assert cluster["generation"] == 1 and cluster["observed_generation"] == 0
        # Placement comes from the registered network (docs/adr/0013).
        assert cluster["environment"] == {"id": network["environment_id"], "name": "production", "type": "PRODUCTION"}
        assert cluster["network"]["id"] == network["id"] and cluster["network"]["name"] == "prod-network"
        assert cluster["network"]["vpc"] == "projects/acme-prod/global/networks/prod-vpc"
        assert cluster["network"]["subnets"][0]["id"] == "projects/acme-prod/regions/asia-south1/subnetworks/db-subnet"
        assert cluster["region"] == "asia-south1" and cluster["cloud_account_id"] == network["cloud_account_id"]
        assert cluster["zones"] == ["asia-south1-a", "asia-south1-b", "asia-south1-c"]

    def test_version_defaults_to_the_catalog(
        self, client: TestClient, owner: dict[str, str], network: dict[str, Any]
    ) -> None:
        request = cluster_request(network)
        del request["version"]
        body = client.post("/api/v1/clusters", json=request, headers=owner).json()
        assert client.get(f"/api/v1/clusters/{body['cluster_id']}", headers=owner).json()["engine_version"] == "9.5.4"

    @pytest.mark.parametrize(
        ("overrides", "field"),
        [
            ({"name": "Bad Name"}, "name"),
            ({"node_count": 2, "high_availability": True}, "high_availability"),
            ({"zone": "us-central1-a"}, "zone"),
            ({"machine_type": "x9-huge-1"}, "machine_type"),
            ({"version": "latest"}, "version"),
            ({"version": "9.x"}, "version"),
            ({"version": "9.5"}, "version"),
            ({"version": "2.x"}, "version"),
            ({"storage_type": "floppy"}, "storage_type"),
            ({"node_count": 0}, "node_count"),
            ({"project_id": "acme-prod"}, "project_id"),
            ({"cloud_provider": "gcp"}, "cloud_provider"),
            ({"region": "us-central1"}, "region"),
            ({"cloud_account_id": str(uuid.uuid4())}, "cloud_account_id"),
            ({"environment_id": str(uuid.uuid4())}, "environment_id"),
            ({"network_id": str(uuid.uuid4())}, "network_id"),
        ],
    )
    def test_validation(
        self,
        client: TestClient,
        owner: dict[str, str],
        network: dict[str, Any],
        overrides: dict[str, Any],
        field: str,
    ) -> None:
        response = client.post("/api/v1/clusters", json=cluster_request(network, **overrides), headers=owner)
        assert response.status_code == 422, response.text
        assert field in response.json()["error"]["details"]["fields"]

    def test_unsupported_engine(self, client: TestClient, owner: dict[str, str], network: dict[str, Any]) -> None:
        response = client.post("/api/v1/clusters", json=cluster_request(network, engine="redis"), headers=owner)
        assert response.status_code == 422
        assert "engine" in response.json()["error"]["details"]["fields"]

    def test_requires_an_available_network(
        self, client: TestClient, owner: dict[str, str], cloud_account: dict[str, Any], environment: dict[str, Any]
    ) -> None:
        broken = register_network(client, owner, environment["id"], cloud_account["id"], vpc="notfound-vpc")
        assert broken["status"] == "FAILED"
        response = client.post("/api/v1/clusters", json=cluster_request(broken, zone=None), headers=owner)
        assert response.status_code == 422 and response.json()["error"]["code"] == "NETWORK_NOT_AVAILABLE"

    def test_network_must_belong_to_the_environment(
        self, client: TestClient, owner: dict[str, str], network: dict[str, Any]
    ) -> None:
        test_env = client.post("/api/v1/environments", json={"name": "test", "type": "TEST"}, headers=owner).json()
        response = client.post(
            "/api/v1/clusters", json=cluster_request(network, environment_id=test_env["id"]), headers=owner
        )
        assert response.status_code == 422 and "network_id" in response.json()["error"]["details"]["fields"]

    def test_single_zone_placement_uses_the_chosen_zone(
        self, client: TestClient, owner: dict[str, str], network: dict[str, Any], run_worker: RunWorker
    ) -> None:
        request = cluster_request(network, node_count=1, high_availability=False, zone="asia-south1-b")
        created = client.post("/api/v1/clusters", json=request, headers=owner).json()
        run_worker()
        cluster = client.get(f"/api/v1/clusters/{created['cluster_id']}", headers=owner).json()
        assert cluster["lifecycle"] == "ACTIVE" and cluster["zones"] == ["asia-south1-b"]
        assert [n["zone"] for n in cluster["nodes"]] == ["asia-south1-b"]

    def test_subnet_capacity(
        self, client: TestClient, owner: dict[str, str], cloud_account: dict[str, Any], environment: dict[str, Any]
    ) -> None:
        small = register_network(client, owner, environment["id"], cloud_account["id"], subnets=["small-subnet"])
        assert small["details"]["subnets"][0]["available_ips"] == 12
        response = client.post("/api/v1/clusters", json=cluster_request(small, node_count=20), headers=owner)
        assert response.status_code == 422 and "node_count" in response.json()["error"]["details"]["fields"]

    def test_duplicate_name(
        self, client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any], network: dict[str, Any]
    ) -> None:
        response = client.post("/api/v1/clusters", json=cluster_request(network), headers=owner)
        assert response.status_code == 409

    def test_idempotency_key(self, client: TestClient, owner: dict[str, str], network: dict[str, Any]) -> None:
        headers = {**owner, "Idempotency-Key": "create-123"}
        first = client.post("/api/v1/clusters", json=cluster_request(network), headers=headers)
        second = client.post("/api/v1/clusters", json=cluster_request(network), headers=headers)
        assert first.status_code == second.status_code == 202
        assert first.json() == second.json()
        assert len(client.get("/api/v1/clusters", headers=owner).json()) == 1
        different = client.post("/api/v1/clusters", json=cluster_request(network, node_count=5), headers=headers)
        assert different.status_code == 409
        assert different.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


class TestScale:
    def test_one_mutation_at_a_time(
        self, client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any]
    ) -> None:
        cluster_id = running_cluster["id"]
        first = client.post(f"/api/v1/clusters/{cluster_id}/scale", json={"node_count": 5}, headers=owner)
        assert first.status_code == 202
        again = client.post(f"/api/v1/clusters/{cluster_id}/scale", json={"node_count": 7}, headers=owner)
        assert again.status_code == 409
        check = client.post(f"/api/v1/clusters/{cluster_id}/health-check", headers=owner)
        assert check.status_code == 202, "health checks do not conflict with mutations"

    def test_scale_down_is_rejected(
        self, client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any]
    ) -> None:
        cluster_id = running_cluster["id"]
        down = client.post(f"/api/v1/clusters/{cluster_id}/scale", json={"node_count": 2}, headers=owner)
        assert down.status_code == 422
        assert down.json()["error"]["code"] == "SCALE_DOWN_NOT_SUPPORTED"
        assert "node_count" in down.json()["error"]["details"]["fields"]
        same = client.post(f"/api/v1/clusters/{cluster_id}/scale", json={"node_count": 3}, headers=owner)
        assert same.status_code == 422 and same.json()["error"]["code"] == "NO_CHANGE"
        cluster = client.get(f"/api/v1/clusters/{cluster_id}", headers=owner).json()
        assert cluster["lifecycle"] == "ACTIVE" and cluster["generation"] == 1

    def test_scale_updates_desired_state_first(
        self, client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any]
    ) -> None:
        cluster_id = running_cluster["id"]
        response = client.post(f"/api/v1/clusters/{cluster_id}/scale", json={"node_count": 5}, headers=owner)
        assert response.json()["lifecycle"] == "SCALING"
        cluster = client.get(f"/api/v1/clusters/{cluster_id}", headers=owner).json()
        assert cluster["desired_state"]["nodes"]["count"] == 5
        assert cluster["actual_state"]["nodes"]["count"] == 3
        assert cluster["generation"] == 2 and cluster["observed_generation"] == 1
        assert cluster["active_operation"]["operation_type"] == "SCALE_CLUSTER"

    def test_scale_needs_an_active_cluster(
        self, client: TestClient, owner: dict[str, str], network: dict[str, Any]
    ) -> None:
        created = client.post("/api/v1/clusters", json=cluster_request(network), headers=owner).json()
        response = client.post(f"/api/v1/clusters/{created['cluster_id']}/scale", json={"node_count": 5}, headers=owner)
        assert response.status_code == 409

    def test_failed_scale_keeps_the_cluster_active_and_can_be_retried(
        self,
        client: TestClient,
        owner: dict[str, str],
        running_cluster: dict[str, Any],
        run_worker: RunWorker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original = MockDataPlane.create_instance

        def broken(self: MockDataPlane, **kwargs: Any) -> Any:
            instance = original(self, **kwargs)
            if kwargs["node_name"] == "node-5":
                self.set_fault(kwargs["cluster_id"], "node-5", "bootstrap_fail")
            return instance

        monkeypatch.setattr(MockDataPlane, "create_instance", broken)
        cluster_id = running_cluster["id"]
        scale = client.post(f"/api/v1/clusters/{cluster_id}/scale", json={"node_count": 5}, headers=owner).json()
        run_worker()
        op = client.get(f"/api/v1/operations/{scale['operation_id']}", headers=owner).json()
        assert op["status"] == "FAILED" and op["error_code"] == "BOOTSTRAP_FAILED"
        cluster = client.get(f"/api/v1/clusters/{cluster_id}", headers=owner).json()
        assert cluster["lifecycle"] == "ACTIVE" and "retry" in cluster["status_message"]
        assert cluster["desired_state"]["nodes"]["count"] == 5 and cluster["observed_generation"] == 1

        monkeypatch.setattr(MockDataPlane, "create_instance", original)
        client.post(
            f"/api/v1/mock/clusters/{cluster_id}/faults", json={"node_name": "node-5", "fault": "clear"}, headers=owner
        )
        retry = client.post(f"/api/v1/operations/{scale['operation_id']}/retry", headers=owner)
        assert retry.status_code == 202
        run_worker()
        again = client.get(f"/api/v1/operations/{retry.json()['id']}", headers=owner).json()
        assert again["status"] == "COMPLETED", again["error"]
        assert again["retry_of"] == scale["operation_id"]
        cluster = client.get(f"/api/v1/clusters/{cluster_id}", headers=owner).json()
        assert [n["name"] for n in cluster["nodes"]] == ["node-1", "node-2", "node-3", "node-4", "node-5"]
        assert cluster["observed_generation"] == cluster["generation"] == 2


class TestDelete:
    def test_requires_typed_confirmation(
        self, client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any]
    ) -> None:
        cluster_id = running_cluster["id"]
        for params in ({}, {"confirm": "wrong-name"}):
            response = client.delete(f"/api/v1/clusters/{cluster_id}", params=params, headers=owner)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
            assert "confirm" in response.json()["error"]["details"]["fields"]
        assert client.get(f"/api/v1/clusters/{cluster_id}", headers=owner).json()["lifecycle"] == "ACTIVE"
        response = delete_cluster(client, owner, running_cluster)
        assert response.status_code == 202 and response.json()["lifecycle"] == "DELETING"

    def test_is_idempotent_and_cannot_be_cancelled(
        self, client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any]
    ) -> None:
        first = delete_cluster(client, owner, running_cluster).json()
        second = delete_cluster(client, owner, running_cluster)
        assert second.status_code == 202 and second.json() == first
        cancel = client.post(f"/api/v1/operations/{first['operation_id']}/cancel", headers=owner)
        assert cancel.status_code == 409 and cancel.json()["error"]["code"] == "DELETE_NOT_CANCELLABLE"
        operation = client.get(f"/api/v1/operations/{first['operation_id']}", headers=owner).json()
        assert operation["cancellable"] is False

    def test_preempts_a_pending_create(
        self, client: TestClient, owner: dict[str, str], network: dict[str, Any], run_worker: RunWorker
    ) -> None:
        created = client.post("/api/v1/clusters", json=cluster_request(network), headers=owner).json()
        cluster = {"id": created["cluster_id"], "name": "production-search"}
        deleted = delete_cluster(client, owner, cluster).json()
        create_op = client.get(f"/api/v1/operations/{created['operation_id']}", headers=owner).json()
        assert create_op["status"] == "CANCELLED"
        assert create_op["error_message"] == "Superseded by the deletion requested by owner@acme.example"
        run_worker()
        assert client.get(f"/api/v1/operations/{deleted['operation_id']}", headers=owner).json()["status"] == (
            "COMPLETED"
        )
        history = client.get("/api/v1/clusters?include_deleted=true", headers=owner).json()
        assert history[0]["lifecycle"] == "DELETED"
        assert audit_actions(client, owner, created["cluster_id"]) == [
            "CLUSTER_CREATE_STARTED",
            "OPERATION_CANCELLED",
            "CLUSTER_DELETE_STARTED",
            "CLUSTER_DELETE_COMPLETED",
        ]

    def test_preempts_a_running_scale_at_its_next_safe_point(
        self,
        client: TestClient,
        owner: dict[str, str],
        running_cluster: dict[str, Any],
        run_worker: RunWorker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original = MockGcpProvider.plan_infrastructure
        requested: dict[str, Any] = {}

        def plan_while_user_deletes(self: MockGcpProvider, *args: Any, **kwargs: Any) -> Any:
            summary = original(self, *args, **kwargs)
            if not requested:
                requested.update(delete_cluster(client, owner, running_cluster).json())
                cluster = client.get(f"/api/v1/clusters/{running_cluster['id']}", headers=owner).json()
                requested["lifecycle_during_scale"] = cluster["lifecycle"]
            return summary

        monkeypatch.setattr(MockGcpProvider, "plan_infrastructure", plan_while_user_deletes)
        scale = client.post(
            f"/api/v1/clusters/{running_cluster['id']}/scale", json={"node_count": 5}, headers=owner
        ).json()
        run_worker()

        assert requested["lifecycle_during_scale"] == "DELETING"
        scale_op = client.get(f"/api/v1/operations/{scale['operation_id']}", headers=owner).json()
        assert scale_op["status"] == "CANCELLED"
        assert {s["key"]: s["status"] for s in scale_op["steps"]}["apply"] == "pending", "no VM was added"
        delete_op = client.get(f"/api/v1/operations/{requested['operation_id']}", headers=owner).json()
        assert delete_op["status"] == "COMPLETED", delete_op["error"]
        assert client.get("/api/v1/clusters?include_deleted=true", headers=owner).json()[0]["lifecycle"] == "DELETED"
        cancelled = client.get("/api/v1/audit-logs?action=OPERATION_CANCELLED", headers=owner).json()["items"][0]
        assert cancelled["user"] == "owner@acme.example"
        assert cancelled["details"]["reason"] == "Superseded by the deletion requested by owner@acme.example"

    def test_waits_until_the_preempted_operation_stops(
        self,
        client: TestClient,
        owner: dict[str, str],
        running_cluster: dict[str, Any],
        platform: Platform,
        run_worker: RunWorker,
    ) -> None:
        scale = client.post(
            f"/api/v1/clusters/{running_cluster['id']}/scale", json={"node_count": 5}, headers=owner
        ).json()
        platform.queue.drain()  # type: ignore[attr-defined]
        runner = OperationRunner(platform, "worker-a")
        assert runner.claim(uuid.UUID(scale["operation_id"]))  # the scale is running on worker-a

        deleted = delete_cluster(client, owner, running_cluster).json()
        assert not runner.claim(uuid.UUID(deleted["operation_id"])), "the delete must wait for the scale"
        operation = client.get(f"/api/v1/operations/{deleted['operation_id']}", headers=owner).json()
        assert operation["status"] == "PENDING"
        assert any("Waiting for 1 running operation to stop" in e["message"] for e in operation["log"])
        scale_op = client.get(f"/api/v1/operations/{scale['operation_id']}", headers=owner).json()
        assert scale_op["cancel_requested"] is True

        # worker-a reaches its next safe point, stops, and wakes the delete.
        with session_scope(platform.session_factory) as s:
            op = s.get(Operation, uuid.UUID(scale["operation_id"]))
            assert op is not None
            finalize_operation(s, op, OperationStatus.CANCELLED)
        platform.queue.drain()  # type: ignore[attr-defined]
        wake_waiting(platform, uuid.UUID(running_cluster["id"]))
        assert run_worker() == [deleted["operation_id"]]
        assert client.get(f"/api/v1/operations/{deleted['operation_id']}", headers=owner).json()["status"] == (
            "COMPLETED"
        )


class TestReadEndpoints:
    def test_health_nodes_metrics_events(
        self, client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any]
    ) -> None:
        cluster_id = running_cluster["id"]
        health = client.get(f"/api/v1/clusters/{cluster_id}/health", headers=owner).json()
        assert (health["lifecycle"], health["status"]) == ("ACTIVE", "HEALTHY")
        assert (health["infrastructure"], health["engine"]) == ("HEALTHY", "HEALTHY")
        assert health["nodes_reporting"] == 3
        nodes = client.get(f"/api/v1/clusters/{cluster_id}/nodes", headers=owner).json()
        assert [n["name"] for n in nodes] == ["node-1", "node-2", "node-3"]
        for node in nodes:
            assert (node["lifecycle"], node["health"], node["agent_status"]) == ("ACTIVE", "HEALTHY", "REPORTING")
            assert (node["infrastructure_health"], node["engine_health"]) == ("HEALTHY", "HEALTHY")
            assert node["instance_status"] == "RUNNING" and node["private_ip"] and node["instance_name"]
            assert node["system"].get("cpu_percent") is not None
        metrics = client.get(f"/api/v1/clusters/{cluster_id}/metrics", headers=owner).json()
        assert metrics["current"]["engine"]["cluster_status"] == "green"
        assert metrics["history"], "a sample is recorded when health is evaluated"
        events = client.get(f"/api/v1/clusters/{cluster_id}/events", headers=owner).json()
        assert any(e["event_type"] == "CLUSTER_READY" for e in events)

    def test_engine_catalog(self, client: TestClient, owner: dict[str, str]) -> None:
        engines = client.get("/api/v1/engines", headers=owner).json()
        assert engines[0]["engine"] == "elasticsearch" and engines[0]["default_version"] == "9.5.4"
        (version,) = engines[0]["versions"]
        assert version["version"] == "9.5.4" and version["status"] == "supported" and version["default"] is True
        assert version["distribution"] == "elastic-default" and version["license_review_status"] == "pending"

    def test_operations_listing_and_filters(
        self,
        client: TestClient,
        owner: dict[str, str],
        running_cluster: dict[str, Any],
        run_worker: RunWorker,
    ) -> None:
        cluster_id = running_cluster["id"]
        client.post(f"/api/v1/clusters/{cluster_id}/health-check", headers=owner)
        run_worker()
        page = client.get(f"/api/v1/operations?cluster_id={cluster_id}", headers=owner).json()
        assert page["total"] == 2
        assert {o["operation_type"] for o in page["items"]} == {"CREATE_CLUSTER", "HEALTH_CHECK"}
        only_checks = client.get("/api/v1/operations?type=HEALTH_CHECK", headers=owner).json()
        assert only_checks["total"] == 1 and only_checks["items"][0]["result"]["state"] == "HEALTHY"
        assert client.get("/api/v1/operations?limit=1", headers=owner).json()["total"] == 2


def test_changes_need_a_connected_account(
    client: TestClient,
    owner: dict[str, str],
    running_cluster: dict[str, Any],
    run_worker: RunWorker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def revoked(self: MockGcpProvider, account: Any) -> CredentialValidationResult:
        return CredentialValidationResult(valid=False, checks=[], error={"code": "GCP_PERMISSION_DENIED"})

    original = MockGcpProvider.validate_credentials
    monkeypatch.setattr(MockGcpProvider, "validate_credentials", revoked)
    client.post(f"/api/v1/cloud-accounts/{running_cluster['cloud_account_id']}/validate", headers=owner)
    scale = client.post(f"/api/v1/clusters/{running_cluster['id']}/scale", json={"node_count": 5}, headers=owner)
    assert scale.status_code == 409 and scale.json()["error"]["code"] == "CLOUD_ACCOUNT_NOT_CONNECTED"
    # Deletion is still possible, so a cluster is never stranded.
    monkeypatch.setattr(MockGcpProvider, "validate_credentials", original)
    assert delete_cluster(client, owner, running_cluster).status_code == 202
    run_worker()
    assert client.get("/api/v1/clusters", headers=owner).json() == []


def test_personal_accounts_are_explained(client: TestClient, owner: dict[str, str]) -> None:
    body = account_request("acme-prod", service_account_email="suraj.singh@purplle.com")
    response = client.post("/api/v1/cloud-accounts", json=body, headers=owner)
    assert response.status_code == 422
    assert "is a user account" in response.json()["error"]["details"]["fields"]["service_account_email"]
