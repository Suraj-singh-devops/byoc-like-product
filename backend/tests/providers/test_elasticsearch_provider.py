from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.cluster_spec import ClusterSpec
from app.domain.errors import NotSupported, ValidationFailed
from app.domain.health import HealthThresholds, NodeObservation
from app.domain.reports import NodeReport
from app.domain.states import ClusterHealth, NodeHealth
from app.providers.cloud.base import MachineType
from app.providers.database.base import HealthContext
from app.providers.database.elasticsearch import ElasticsearchProvider

es = ElasticsearchProvider()
ZONES = ["asia-south1-a", "asia-south1-b", "asia-south1-c"]


def spec(**overrides: Any) -> ClusterSpec:
    values: dict[str, Any] = {
        "name": "production-search",
        "engine": "elasticsearch",
        "version": "9.5.4",
        "cloud_provider": "gcp",
        "cloud_account_id": str(uuid.uuid4()),
        "project_id": "customer-prod",
        "region": "asia-south1",
        "zone": "asia-south1-a",
        "machine_type": "e2-standard-8",
        "node_count": 3,
        "storage_gb": 500,
        "storage_type": "pd-balanced",
        "high_availability": True,
    }
    values.update(overrides)
    return ClusterSpec(**values)


class TestVersions:
    def test_default_comes_from_the_catalog(self) -> None:
        assert es.resolve_version(None) == "9.5.4"
        assert es.resolve_version("") == "9.5.4"
        assert es.resolve_version("9.5.4") == "9.5.4"

    @pytest.mark.parametrize("given", ["latest", "9", "9.x", "9.5", "9.5.*", "v9.5.4", "9.5.3", "8.19.22", "2.x"])
    def test_only_exact_catalog_versions(self, given: str) -> None:
        with pytest.raises(ValidationFailed) as excinfo:
            es.resolve_version(given)
        assert "9.5.4" in excinfo.value.details["fields"]["version"]

    def test_catalog_exposes_status_and_license_review(self) -> None:
        (version,) = es.catalog().versions
        assert (version.version, version.status, version.default) == ("9.5.4", "supported", True)
        assert version.license_review_status == "pending" and version.distribution == "elastic-default"


class TestValidation:
    def test_ha_needs_three_nodes(self) -> None:
        with pytest.raises(ValidationFailed) as excinfo:
            es.validate(spec(node_count=2, high_availability=True))
        assert "high_availability" in excinfo.value.details["fields"]

    def test_single_node_without_ha_is_fine(self) -> None:
        es.validate(spec(node_count=1, high_availability=False))

    def test_machine_needs_enough_memory(self) -> None:
        with pytest.raises(ValidationFailed) as excinfo:
            es.validate(spec(), MachineType("e2-small", 2, 2))
        assert "machine_type" in excinfo.value.details["fields"]

    def test_minimum_storage(self) -> None:
        with pytest.raises(ValidationFailed):
            es.validate(spec(storage_gb=10))


class TestTopology:
    def test_ha_spreads_masters_over_zones(self) -> None:
        plan = es.provision(spec(), ZONES)
        assert [(n.name, n.zone) for n in plan.nodes] == [
            ("node-1", "asia-south1-a"),
            ("node-2", "asia-south1-b"),
            ("node-3", "asia-south1-c"),
        ]
        assert all("master" in n.roles for n in plan.nodes)
        assert plan.settings["initial_master_nodes"] == ["node-1", "node-2", "node-3"]
        assert plan.settings["seed_nodes"] == ["node-1", "node-2", "node-3"]
        assert plan.settings["zone_awareness"] is True
        assert plan.settings["version"] == "9.5.4"
        package = plan.settings["package"]
        assert package["apt_repository"] == "https://artifacts.elastic.co/packages/9.x/apt"
        assert package["signing_key_fingerprint"] == "46095ACC8548582C1A2699A9D27D666CD88E42B4"
        assert set(package["sha256"]) == {"amd64", "arm64"}

    def test_only_first_three_nodes_are_master_eligible(self) -> None:
        plan = es.provision(spec(node_count=5), ZONES)
        assert [n.roles for n in plan.nodes][3:] == [("data", "ingest"), ("data", "ingest")]
        assert plan.settings["seed_nodes"] == ["node-1", "node-2", "node-3"]

    def test_single_zone_has_no_awareness(self) -> None:
        plan = es.provision(spec(node_count=1, high_availability=False), ["asia-south1-a"])
        assert plan.settings["zone_awareness"] is False
        assert plan.settings["initial_master_nodes"] == ["node-1"]


class TestScaling:
    def current(self, count: int) -> list[Any]:
        return es.provision(spec(node_count=count), ZONES).nodes

    def test_scale_up_adds_next_ordinals(self) -> None:
        plan = es.scale(spec(), self.current(3), 5, ZONES, {"initial_master_nodes": ["node-1", "node-2", "node-3"]})
        assert [n.name for n in plan.add] == ["node-4", "node-5"]
        assert [n.zone for n in plan.add] == ["asia-south1-a", "asia-south1-b"]
        assert [n.name for n in plan.nodes] == ["node-1", "node-2", "node-3", "node-4", "node-5"]

    def test_initial_masters_never_change(self) -> None:
        single = spec(node_count=1, high_availability=False)
        current = es.provision(single, ZONES).nodes
        plan = es.scale(single, current, 3, ZONES, {"initial_master_nodes": ["node-1"]})
        # New master-eligible nodes join the existing cluster; they must not bootstrap a new one.
        assert plan.settings["initial_master_nodes"] == ["node-1"]
        assert plan.settings["seed_nodes"] == ["node-1", "node-2", "node-3"]

    def test_scale_down_is_not_supported(self) -> None:
        with pytest.raises(ValidationFailed) as excinfo:
            es.scale(spec(node_count=5), self.current(5), 3, ZONES, None)
        assert excinfo.value.code == "SCALE_DOWN_NOT_SUPPORTED"

    def test_same_size_is_no_change(self) -> None:
        with pytest.raises(ValidationFailed) as excinfo:
            es.scale(spec(), self.current(3), 3, ZONES, None)
        assert excinfo.value.code == "NO_CHANGE"

    def test_too_many_nodes(self) -> None:
        with pytest.raises(ValidationFailed):
            es.scale(spec(), self.current(3), 31, ZONES, None)


def es_report(
    name: str, *, status: str | None = "green", nodes: int = 3, reachable: bool = True, **engine: Any
) -> NodeReport:
    body: dict[str, Any] = {
        "reachable": reachable,
        "version": "9.5.4",
        "cluster_status": status,
        "number_of_nodes": nodes,
        "unassigned_shards": 0,
        "relocating_shards": 0,
        "is_master": name == "node-1",
        "jvm_heap_percent": 50.0,
        "search_rate": 10.0,
        "indexing_rate": 20.0,
        "active_shards": 24,
    }
    body.update(engine)
    return NodeReport.model_validate(
        {
            "node_name": name,
            "system": {
                "cpu_percent": 40.0,
                "memory_percent": 60.0,
                "disk_percent": 50.0,
                "disk_total_bytes": 100,
                "disk_used_bytes": 50,
                "network_rx_bytes_per_sec": 5.0,
                "network_tx_bytes_per_sec": 3.0,
            },
            "engine": body,
        }
    )


def observe(name: str, report: NodeReport | None, *, status: str = "RUNNING", age: float = 5) -> NodeObservation:
    ordinal = int(name.split("-")[1])
    return NodeObservation(
        name, ordinal, instance_status=status, report=report, report_age_seconds=age if report else None
    )


def health(observations: list[NodeObservation], expected: list[str] | None = None) -> Any:
    ctx = HealthContext(
        expected_nodes=expected or [o.name for o in observations],
        high_availability=True,
        thresholds=HealthThresholds(),
        now=datetime.now(UTC),
    )
    return es.health(ctx, observations)


class TestHealth:
    def test_green_cluster_is_healthy(self) -> None:
        result = health([observe(n, es_report(n)) for n in ("node-1", "node-2", "node-3")])
        assert result.state == ClusterHealth.HEALTHY
        assert (result.infrastructure, result.engine) == (ClusterHealth.HEALTHY, ClusterHealth.HEALTHY)
        assert result.engine_status == "green"
        assert result.nodes_reporting == 3

    def test_one_failed_node_makes_the_cluster_degraded(self) -> None:
        observations = [
            observe("node-1", es_report("node-1", status="yellow", nodes=2, unassigned_shards=8)),
            observe("node-2", None, status="TERMINATED"),
            observe("node-3", es_report("node-3", status="yellow", nodes=2, unassigned_shards=8)),
        ]
        result = health(observations)
        assert result.state == ClusterHealth.DEGRADED
        assert result.node("node-2").state == NodeHealth.UNHEALTHY
        assert any("Node missing: 2/3" in r for r in result.reasons)

    def test_majority_down_is_unhealthy(self) -> None:
        observations = [
            observe("node-1", es_report("node-1", status=None, nodes=1, error="master_not_discovered_exception")),
            observe("node-2", None, status="TERMINATED"),
            observe("node-3", None, status="TERMINATED"),
        ]
        assert health(observations).state == ClusterHealth.UNHEALTHY

    def test_red_is_unhealthy(self) -> None:
        assert health([observe("node-1", es_report("node-1", status="red", nodes=1))]).state == ClusterHealth.UNHEALTHY

    def test_single_node_yellow_is_expected(self) -> None:
        result = health([observe("node-1", es_report("node-1", status="yellow", nodes=1, unassigned_shards=4))])
        assert result.state == ClusterHealth.HEALTHY
        assert "no redundancy" in result.warnings[0]

    def test_elasticsearch_down_on_a_running_vm(self) -> None:
        observations = [
            observe("node-1", es_report("node-1")),
            observe("node-2", es_report("node-2", reachable=False, error="connection refused")),
            observe("node-3", es_report("node-3")),
        ]
        result = health(observations)
        node = result.node("node-2")
        assert (node.state, node.infrastructure, node.engine) == (
            NodeHealth.UNHEALTHY,
            NodeHealth.HEALTHY,
            NodeHealth.UNHEALTHY,
        )
        assert "connection refused" in node.reasons[0]
        assert (result.infrastructure, result.engine, result.state) == (
            ClusterHealth.HEALTHY,
            ClusterHealth.DEGRADED,
            ClusterHealth.DEGRADED,
        )

    def test_silent_agent_degrades_but_is_not_a_dead_node(self) -> None:
        observations = [
            observe("node-1", es_report("node-1")),
            observe("node-2", es_report("node-2")),
            observe("node-3", es_report("node-3"), age=600),
        ]
        result = health(observations)
        assert result.node("node-3").state == NodeHealth.UNKNOWN
        assert result.state == ClusterHealth.DEGRADED
        assert any("No recent report from: node-3" in r for r in result.reasons)

    def test_heap_pressure(self) -> None:
        result = health(
            [
                observe(n, es_report(n, jvm_heap_percent=95.0 if n == "node-2" else 50.0))
                for n in ("node-1", "node-2", "node-3")
            ]
        )
        assert result.node("node-2").state == NodeHealth.UNHEALTHY
        assert result.state == ClusterHealth.DEGRADED

    def test_high_heap_is_a_warning_only(self) -> None:
        result = health([observe(n, es_report(n, jvm_heap_percent=88.0)) for n in ("node-1", "node-2", "node-3")])
        assert result.state == ClusterHealth.HEALTHY
        assert result.warnings == [f"{n}: JVM heap high: 88%" for n in ("node-1", "node-2", "node-3")]

    def test_no_data_yet_is_unknown(self) -> None:
        assert health([observe("node-1", None)]).state == ClusterHealth.UNKNOWN


class TestMetrics:
    def test_metrics_aggregate(self) -> None:
        metrics = es.metrics([observe(n, es_report(n)) for n in ("node-1", "node-2")], stale_after=90)
        assert metrics.nodes_reporting == 2
        assert metrics.cpu_percent == 40.0
        assert metrics.disk_percent == 50.0
        assert metrics.engine["search_rate"] == 20.0
        assert metrics.engine["jvm_heap_percent"] == 50.0
        assert metrics.engine["cluster_status"] == "green"

    def test_stale_reports_are_ignored(self) -> None:
        metrics = es.metrics([observe("node-1", es_report("node-1"), age=600)], stale_after=90)
        assert metrics.nodes_reporting == 0
        assert metrics.cpu_percent is None

    def test_future_capabilities_are_explicit(self) -> None:
        with pytest.raises(NotSupported):
            es.backup(spec())
        with pytest.raises(NotSupported):
            es.upgrade(spec(), "9.6.0")
