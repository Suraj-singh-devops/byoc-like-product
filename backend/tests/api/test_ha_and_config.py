"""Dedicated master/data/coordinating layout (docs/adr/0016) and configuration changes (docs/adr/0017)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import cluster_request

RunWorker = Callable[[], list[str]]
Login = Callable[[str], dict[str, str]]

HA_GROUPS = {
    "master": {"count": 3, "machine_type": "e2-standard-4", "storage_gb": 20},
    "data": {"count": 3, "machine_type": "e2-standard-8", "storage_gb": 500},
    "coordinating": {"count": 2, "machine_type": "e2-standard-4", "storage_gb": 20},
}


def ha_request(network: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body = cluster_request(network, name="ha-search", layout="dedicated", node_groups=HA_GROUPS)
    for key in ("machine_type", "node_count", "storage_gb"):
        body.pop(key)
    body.update(overrides)
    return body


@pytest.fixture
def ha_cluster(
    client: TestClient, owner: dict[str, str], network: dict[str, Any], run_worker: RunWorker
) -> dict[str, Any]:
    response = client.post("/api/v1/clusters", json=ha_request(network), headers=owner)
    assert response.status_code == 202, response.text
    run_worker()
    detail = client.get(f"/api/v1/clusters/{response.json()['cluster_id']}", headers=owner).json()
    assert detail["lifecycle"] == "ACTIVE", detail
    return detail


def nodes_of(client: TestClient, headers: dict[str, str], cluster_id: str) -> list[dict[str, Any]]:
    return client.get(f"/api/v1/clusters/{cluster_id}/nodes", headers=headers).json()


def put_config(
    client: TestClient, headers: dict[str, str], cluster_id: str, settings: dict[str, Any], key: str | None = None
) -> Any:
    extra = {"Idempotency-Key": key} if key else {}
    return client.put(
        f"/api/v1/clusters/{cluster_id}/config", json={"settings": settings}, headers={**headers, **extra}
    )


class TestDedicatedLayout:
    def test_creates_master_data_and_coordinating_groups(
        self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any]
    ) -> None:
        assert ha_cluster["layout"] == "dedicated"
        assert ha_cluster["health"] == "HEALTHY"
        assert ha_cluster["endpoint"], "the internal load balancer address is reported"
        groups = {g["name"]: g["count"] for g in ha_cluster["node_groups"]}
        assert groups == {"master": 3, "data": 3, "coordinating": 2}

        nodes = nodes_of(client, owner, ha_cluster["id"])
        by_group: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
            by_group.setdefault(node["node_group"], []).append(node)
        assert {k: len(v) for k, v in by_group.items()} == groups
        # One master per zone: a quorum survives the loss of any zone.
        assert len({n["zone"] for n in by_group["master"]}) == 3
        assert {n["machine_type"] for n in by_group["data"]} == {"e2-standard-8"}
        assert all(n["name"].startswith("coord-") for n in by_group["coordinating"])

    def test_requires_exactly_three_masters(
        self, client: TestClient, owner: dict[str, str], network: dict[str, Any]
    ) -> None:
        groups = {**HA_GROUPS, "master": {**HA_GROUPS["master"], "count": 2}}
        response = client.post("/api/v1/clusters", json=ha_request(network, node_groups=groups), headers=owner)
        assert response.status_code == 422
        assert "node_groups.master.count" in response.json()["error"]["details"]["fields"]

    def test_ha_requires_two_coordinating_nodes(
        self, client: TestClient, owner: dict[str, str], network: dict[str, Any]
    ) -> None:
        groups = {**HA_GROUPS, "coordinating": {**HA_GROUPS["coordinating"], "count": 1}}
        response = client.post("/api/v1/clusters", json=ha_request(network, node_groups=groups), headers=owner)
        assert response.status_code == 422
        assert "node_groups.coordinating.count" in response.json()["error"]["details"]["fields"]

    def test_dedicated_layout_needs_node_groups(
        self, client: TestClient, owner: dict[str, str], network: dict[str, Any]
    ) -> None:
        body = ha_request(network)
        body.pop("node_groups")
        assert client.post("/api/v1/clusters", json=body, headers=owner).status_code == 422

    def test_scales_the_data_group_only(
        self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any], run_worker: RunWorker
    ) -> None:
        response = client.post(
            f"/api/v1/clusters/{ha_cluster['id']}/scale", json={"node_count": 4, "group": "data"}, headers=owner
        )
        assert response.status_code == 202, response.text
        run_worker()
        detail = client.get(f"/api/v1/clusters/{ha_cluster['id']}", headers=owner).json()
        assert detail["lifecycle"] == "ACTIVE" and detail["health"] == "HEALTHY"
        assert {g["name"]: g["count"] for g in detail["node_groups"]} == {"master": 3, "data": 4, "coordinating": 2}
        names = {n["name"] for n in nodes_of(client, owner, ha_cluster["id"])}
        assert len(names) == 9

    def test_master_group_cannot_be_scaled(
        self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any]
    ) -> None:
        response = client.post(
            f"/api/v1/clusters/{ha_cluster['id']}/scale", json={"node_count": 5, "group": "master"}, headers=owner
        )
        assert response.status_code in (409, 422), response.text

    def test_initial_config_is_applied(
        self, client: TestClient, owner: dict[str, str], network: dict[str, Any], run_worker: RunWorker
    ) -> None:
        body = ha_request(network, config={"search.max_buckets": 20000})
        response = client.post("/api/v1/clusters", json=body, headers=owner)
        assert response.status_code == 202, response.text
        run_worker()
        config = client.get(f"/api/v1/clusters/{response.json()['cluster_id']}/config", headers=owner).json()
        assert config["desired"] == {"search.max_buckets": 20000}
        assert config["pending"] == {"dynamic": [], "static": []}


class TestConfiguration:
    def test_catalog_lists_dynamic_and_static_settings(
        self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any]
    ) -> None:
        config = client.get(f"/api/v1/clusters/{ha_cluster['id']}/config", headers=owner).json()
        scopes = {s["key"]: s["scope"] for s in config["settings"]}
        assert scopes["cluster.routing.allocation.disk.watermark.low"] == "dynamic"
        assert scopes["thread_pool.write.queue_size"] == "static"
        # Security, network and discovery settings are never offered.
        assert not any(k.startswith(("xpack.", "network.", "discovery.", "path.")) for k in scopes)
        assert config["desired"] == {} and config["pending"] == {"dynamic": [], "static": []}

    def test_dynamic_change_applies_without_restart(
        self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any], run_worker: RunWorker
    ) -> None:
        response = put_config(client, owner, ha_cluster["id"], {"cluster.max_shards_per_node": 2000})
        assert response.status_code == 202, response.text
        assert response.json()["lifecycle"] == "UPDATING"
        run_worker()
        op = client.get(f"/api/v1/operations/{response.json()['operation_id']}", headers=owner).json()
        assert op["status"] == "COMPLETED", op
        assert op["operation_type"] == "UPDATE_CONFIG"
        detail = client.get(f"/api/v1/clusters/{ha_cluster['id']}", headers=owner).json()
        assert detail["lifecycle"] == "ACTIVE" and detail["health"] == "HEALTHY"
        config = client.get(f"/api/v1/clusters/{ha_cluster['id']}/config", headers=owner).json()
        assert config["desired"] == {"cluster.max_shards_per_node": 2000}
        assert config["applied"] == {"cluster.max_shards_per_node": 2000}
        assert config["pending"] == {"dynamic": [], "static": []}
        assert op["result"]["restarted"] == []
        assert not any(e["message"].startswith("Restarting ") for e in op["log"])

    def test_static_change_rolls_every_node_masters_last(
        self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any], run_worker: RunWorker
    ) -> None:
        response = put_config(client, owner, ha_cluster["id"], {"thread_pool.write.queue_size": 20000})
        assert response.status_code == 202, response.text
        run_worker()
        op = client.get(f"/api/v1/operations/{response.json()['operation_id']}", headers=owner).json()
        assert op["status"] == "COMPLETED", op
        order = op["result"]["restarted"]
        assert len(order) == 8, order
        groups = [n.split("-")[0] for n in order]
        # Data first, coordinating next, masters last (the elected master is the very last one).
        assert groups == ["data"] * 3 + ["coord"] * 2 + ["master"] * 3, order
        restarts = [e["message"] for e in op["log"] if e["message"].startswith("Restarting ")]
        assert [m.split()[1] for m in restarts] == order
        config = client.get(f"/api/v1/clusters/{ha_cluster['id']}/config", headers=owner).json()
        assert config["applied"] == {"thread_pool.write.queue_size": 20000}

    def test_reset_to_default(
        self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any], run_worker: RunWorker
    ) -> None:
        put_config(client, owner, ha_cluster["id"], {"search.max_buckets": 20000})
        run_worker()
        response = put_config(client, owner, ha_cluster["id"], {"search.max_buckets": None})
        assert response.status_code == 202, response.text
        run_worker()
        config = client.get(f"/api/v1/clusters/{ha_cluster['id']}/config", headers=owner).json()
        assert config["desired"] == {} and config["applied"] == {}

    @pytest.mark.parametrize(
        ("changes", "field"),
        [
            ({"xpack.security.enabled": False}, "xpack.security.enabled"),
            ({"network.host": "0.0.0.0"}, "network.host"),  # noqa: S104 - must be refused
            ({"search.max_buckets": 10}, "search.max_buckets"),
            ({"cluster.routing.allocation.enable": "sometimes"}, "cluster.routing.allocation.enable"),
            (
                {"cluster.routing.allocation.disk.watermark.low": "92%"},
                "cluster.routing.allocation.disk.watermark.low",
            ),
        ],
    )
    def test_rejects_invalid_or_unmanaged_settings(
        self,
        client: TestClient,
        owner: dict[str, str],
        ha_cluster: dict[str, Any],
        changes: dict[str, Any],
        field: str,
    ) -> None:
        response = put_config(client, owner, ha_cluster["id"], changes)
        assert response.status_code == 422, response.text
        assert field in response.json()["error"]["details"]["fields"]

    def test_no_change_is_refused(self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any]) -> None:
        response = put_config(client, owner, ha_cluster["id"], {"search.max_buckets": 65536})
        assert response.status_code in (409, 422), response.text

    def test_idempotent_request(self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any]) -> None:
        first = put_config(client, owner, ha_cluster["id"], {"search.max_buckets": 30000}, key="cfg-1")
        second = put_config(client, owner, ha_cluster["id"], {"search.max_buckets": 30000}, key="cfg-1")
        assert first.status_code == second.status_code == 202
        assert first.json()["operation_id"] == second.json()["operation_id"]

    def test_one_change_at_a_time(self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any]) -> None:
        assert put_config(client, owner, ha_cluster["id"], {"search.max_buckets": 30000}).status_code == 202
        assert put_config(client, owner, ha_cluster["id"], {"search.max_buckets": 40000}).status_code == 409

    def test_combined_cluster_can_be_configured(
        self, client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any], run_worker: RunWorker
    ) -> None:
        response = put_config(client, owner, running_cluster["id"], {"indices.memory.index_buffer_size": "20%"})
        assert response.status_code == 202, response.text
        run_worker()
        op = client.get(f"/api/v1/operations/{response.json()['operation_id']}", headers=owner).json()
        assert op["status"] == "COMPLETED", op

    def test_audited(
        self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any], run_worker: RunWorker
    ) -> None:
        put_config(client, owner, ha_cluster["id"], {"search.max_buckets": 30000})
        run_worker()
        items = client.get(f"/api/v1/audit-logs?resource_id={ha_cluster['id']}&limit=100", headers=owner).json()[
            "items"
        ]
        actions = {e["action"] for e in items}
        assert {"CLUSTER_CONFIG_UPDATE_STARTED", "CLUSTER_CONFIG_UPDATED"} <= actions


class TestConfigurationPermissions:
    @pytest.mark.parametrize(("email", "allowed"), [("admin@acme.example", True), ("operator@acme.example", False)])
    def test_owner_and_admin_only(
        self, client: TestClient, login: Login, ha_cluster: dict[str, Any], email: str, allowed: bool
    ) -> None:
        headers = login(email)
        assert client.get(f"/api/v1/clusters/{ha_cluster['id']}/config", headers=headers).status_code == 200
        response = put_config(client, headers, ha_cluster["id"], {"search.max_buckets": 30000})
        assert response.status_code == (202 if allowed else 403), response.text

    def test_viewer_can_read(self, client: TestClient, login: Login, ha_cluster: dict[str, Any]) -> None:
        viewer = login("viewer@acme.example")
        assert client.get(f"/api/v1/clusters/{ha_cluster['id']}/config", headers=viewer).status_code == 200
        assert put_config(client, viewer, ha_cluster["id"], {"search.max_buckets": 30000}).status_code == 403


class TestElasticsearchYml:
    """Any elasticsearch.yml setting except the ones the platform owns (docs/adr/0018)."""

    def test_custom_settings_roll_out_and_show_in_the_file(
        self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any], run_worker: RunWorker
    ) -> None:
        changes = {
            "indices.query.bool.max_clause_count": 8192,
            "xpack.ml.enabled": False,
            "reindex.remote.whitelist": ["10.0.0.5:9200", "10.0.0.6:9200"],
        }
        response = put_config(client, owner, ha_cluster["id"], changes)
        assert response.status_code == 202, response.text
        run_worker()
        op = client.get(f"/api/v1/operations/{response.json()['operation_id']}", headers=owner).json()
        assert op["status"] == "COMPLETED", op
        assert len(op["result"]["restarted"]) == 8
        config = client.get(f"/api/v1/clusters/{ha_cluster['id']}/config", headers=owner).json()
        assert config["custom"] == {
            "indices.query.bool.max_clause_count": "8192",
            "xpack.ml.enabled": "false",
            "reindex.remote.whitelist": "10.0.0.5:9200,10.0.0.6:9200",
        }
        assert config["applied"] == config["desired"]
        data_file = next(f for f in config["config_file"]["files"] if f["group"] == "data")
        assert 'xpack.ml.enabled: "false"' in data_file["user"]
        assert any(line.startswith("xpack.security.enabled: true") for line in data_file["managed"])
        assert "network." in config["config_file"]["reserved_prefixes"]

    @pytest.mark.parametrize(
        "key",
        [
            "network.bind_host",
            "http.port",
            "transport.port",
            "discovery.type",
            "cluster.initial_master_nodes",
            "node.roles",
            "node.attr.rack",
            "path.repo",
            "xpack.security.http.ssl.enabled",
            "cluster.routing.allocation.awareness.attributes",
        ],
    )
    def test_platform_owned_keys_are_refused(
        self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any], key: str
    ) -> None:
        response = put_config(client, owner, ha_cluster["id"], {key: "x"})
        assert response.status_code == 422, response.text
        assert "Managed by the platform" in response.json()["error"]["details"]["fields"][key]

    @pytest.mark.parametrize(
        ("key", "value"),
        [("Bad Key", "1"), ("nodot", "1"), ("indices.foo", "a\nnetwork.host: 0.0.0.0"), ("indices.foo", "")],
    )
    def test_invalid_names_and_values(
        self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any], key: str, value: str
    ) -> None:
        response = put_config(client, owner, ha_cluster["id"], {key: value})
        assert response.status_code == 422, response.text

    def test_a_setting_elasticsearch_refuses_is_rolled_back_on_one_node_only(
        self, client: TestClient, owner: dict[str, str], ha_cluster: dict[str, Any], run_worker: RunWorker
    ) -> None:
        # Simulated Elasticsearch refuses "unknown.*" settings at startup (docs/mock-mode.md).
        response = put_config(client, owner, ha_cluster["id"], {"unknown.setting": "1"})
        assert response.status_code == 202, response.text
        run_worker()
        op = client.get(f"/api/v1/operations/{response.json()['operation_id']}", headers=owner).json()
        assert op["status"] == "FAILED" and op["error_code"] == "CONFIG_REJECTED", op
        assert "unknown setting [unknown.setting]" in op["error"]["reason"]
        restarts = [e["message"] for e in op["log"] if e["message"].startswith("Restarting ")]
        assert restarts == ["Restarting data-1 (1/8)"], restarts

        detail = client.get(f"/api/v1/clusters/{ha_cluster['id']}", headers=owner).json()
        assert detail["lifecycle"] == "ACTIVE" and detail["health"] == "HEALTHY"

        # A retry stops at the same node at once; it never carries the setting to the others.
        retried = client.post(f"/api/v1/operations/{op['id']}/retry", headers=owner)
        assert retried.status_code in (200, 201, 202), retried.text
        run_worker()
        again = client.get(f"/api/v1/operations/{retried.json()['id']}", headers=owner).json()
        assert again["status"] == "FAILED" and again["error_code"] == "CONFIG_REJECTED"
        assert not [e for e in again["log"] if e["message"].startswith("Restarting ") and "data-1" not in e["message"]]

        # Removing the setting brings every node to one configuration again.
        fixed = put_config(client, owner, ha_cluster["id"], {"unknown.setting": None})
        assert fixed.status_code == 202, fixed.text
        run_worker()
        final = client.get(f"/api/v1/operations/{fixed.json()['operation_id']}", headers=owner).json()
        assert final["status"] == "COMPLETED", final
        config = client.get(f"/api/v1/clusters/{ha_cluster['id']}/config", headers=owner).json()
        assert config["desired"] == config["applied"] == {}
