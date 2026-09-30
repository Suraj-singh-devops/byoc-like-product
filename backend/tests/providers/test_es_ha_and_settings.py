"""Dedicated master/data/coordinating topology, role-aware health (docs/adr/0016) and the settings
catalog (docs/adr/0017)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.domain.cluster_spec import DEDICATED, NodeGroupSpec
from app.domain.errors import ValidationFailed
from app.domain.health import HealthThresholds, NodeObservation
from app.domain.states import ClusterHealth
from app.providers.database.base import HealthContext
from app.providers.database.elasticsearch import settings as es_settings
from tests.providers.test_elasticsearch_provider import ZONES, es, es_report, spec

GROUPS = (
    NodeGroupSpec("master", 3, "e2-standard-4", 20),
    NodeGroupSpec("data", 3, "e2-standard-8", 500),
    NodeGroupSpec("coordinating", 2, "e2-standard-4", 20),
)


def ha_spec(**overrides: Any) -> Any:
    return spec(layout=DEDICATED, node_groups=GROUPS, node_count=8, **overrides)


class TestDedicatedTopology:
    def test_groups_roles_and_zones(self) -> None:
        plan = es.provision(ha_spec(), ZONES)
        by_name = {n.name: n for n in plan.nodes}
        assert list(by_name) == [
            "master-1", "master-2", "master-3", "data-1", "data-2", "data-3", "coord-1", "coord-2",
        ]  # fmt: skip
        assert [n.ordinal for n in plan.nodes] == list(range(1, 9))
        assert {by_name[f"master-{i}"].zone for i in (1, 2, 3)} == set(ZONES)
        assert {by_name[f"data-{i}"].zone for i in (1, 2, 3)} == set(ZONES)
        assert by_name["coord-1"].zone != by_name["coord-2"].zone
        assert by_name["master-1"].roles == ("master",)
        assert "data" in by_name["data-1"].roles and "master" not in by_name["data-1"].roles
        assert by_name["coord-1"].roles == ()
        assert by_name["data-1"].machine_type == "e2-standard-8" and by_name["data-1"].storage_gb == 500
        assert plan.settings["initial_master_nodes"] == ["master-1", "master-2", "master-3"]
        assert plan.settings["layout"] == DEDICATED
        assert plan.settings["forced_awareness_zones"] == ZONES

    def test_load_balancer_fronts_coordinating_nodes(self) -> None:
        nodes = es.provision(ha_spec(), ZONES).nodes
        assert es.load_balancer_targets(ha_spec(), nodes) == ["coord-1", "coord-2"]

    def test_scale_grows_one_group_with_new_ordinals(self) -> None:
        current = es.provision(ha_spec(), ZONES).nodes
        plan = es.scale(ha_spec(), current, 5, ZONES, None, "data")
        assert [(n.name, n.ordinal) for n in plan.add] == [("data-4", 9), ("data-5", 10)]
        assert {n.zone for n in plan.add} <= set(ZONES)

    def test_masters_cannot_be_scaled(self) -> None:
        current = es.provision(ha_spec(), ZONES).nodes
        with pytest.raises(ValidationFailed):
            es.scale(ha_spec(), current, 5, ZONES, None, "master")

    @pytest.mark.parametrize(
        ("groups", "field"),
        [
            ((NodeGroupSpec("master", 1, "e2-standard-4", 20), *GROUPS[1:]), "node_groups.master.count"),
            ((GROUPS[0], NodeGroupSpec("data", 1, "e2-standard-8", 500), GROUPS[2]), "node_groups.data.count"),
            ((GROUPS[0], GROUPS[1]), "node_groups.coordinating.count"),
        ],
    )
    def test_ha_rules(self, groups: tuple[NodeGroupSpec, ...], field: str) -> None:
        with pytest.raises(ValidationFailed) as excinfo:
            es.validate(spec(layout=DEDICATED, node_groups=groups))
        assert field in excinfo.value.details["fields"]


def observe(name: str, ordinal: int, *, up: bool = True, **engine: Any) -> NodeObservation:
    report = es_report(name, nodes=8, is_master=name == "master-1", **engine) if up else None
    return NodeObservation(
        name, ordinal, instance_status="RUNNING" if up else "TERMINATED", report=report,
        report_age_seconds=5 if up else None,
    )  # fmt: skip


NAMES = ["master-1", "master-2", "master-3", "data-1", "data-2", "data-3", "coord-1", "coord-2"]
GROUP_OF = {n: {"master": "master", "data": "data", "coord": "coordinating"}[n.split("-")[0]] for n in NAMES}


def ha_health(down: set[str], **engine: Any) -> Any:
    ctx = HealthContext(
        expected_nodes=NAMES,
        high_availability=True,
        thresholds=HealthThresholds(),
        now=datetime.now(UTC),
        node_groups=GROUP_OF,
    )
    observations = [observe(n, i, up=n not in down, **engine) for i, n in enumerate(NAMES, start=1)]
    return es.health(ctx, observations)


class TestRoleAwareHealth:
    def test_all_up_is_healthy(self) -> None:
        assert ha_health(set()).state == ClusterHealth.HEALTHY

    def test_one_master_down_keeps_quorum(self) -> None:
        assert ha_health({"master-2"}, status="green").state == ClusterHealth.DEGRADED

    def test_master_quorum_lost_is_unhealthy(self) -> None:
        result = ha_health({"master-2", "master-3"}, status=None)
        assert result.state == ClusterHealth.UNHEALTHY
        assert any("quorum" in r.lower() for r in result.reasons)

    def test_one_coordinating_node_down_is_degraded(self) -> None:
        assert ha_health({"coord-1"}).state == ClusterHealth.DEGRADED

    def test_all_coordinating_nodes_down_is_unhealthy(self) -> None:
        result = ha_health({"coord-1", "coord-2"})
        assert result.state == ClusterHealth.UNHEALTHY
        assert any("coordinating" in r.lower() for r in result.reasons)

    def test_a_data_node_down_is_degraded(self) -> None:
        assert ha_health({"data-3"}, status="yellow").state == ClusterHealth.DEGRADED


class TestSettings:
    def test_normalizes_values(self) -> None:
        merged = es_settings.merge_config(
            {},
            {
                "cluster.routing.allocation.disk.watermark.low": "80",
                "indices.recovery.max_bytes_per_sec": "100 MB",
                "action.destructive_requires_name": "false",
                "search.max_buckets": "20000",
            },
        )
        assert merged == {
            "cluster.routing.allocation.disk.watermark.low": "80%",
            "indices.recovery.max_bytes_per_sec": "100mb",
            "action.destructive_requires_name": False,
            "search.max_buckets": 20000,
        }

    def test_default_values_are_not_stored(self) -> None:
        assert es_settings.merge_config({"search.max_buckets": 20000}, {"search.max_buckets": 65536}) == {}
        assert es_settings.merge_config({"search.max_buckets": 20000}, {"search.max_buckets": None}) == {}

    def test_unknown_and_out_of_range_are_rejected_together(self) -> None:
        with pytest.raises(ValidationFailed) as excinfo:
            es_settings.merge_config(
                {}, {"xpack.security.enabled": False, "search.max_buckets": 5, "byoc.jvm.heap_percent": 90}
            )
        assert set(excinfo.value.details["fields"]) == {
            "xpack.security.enabled",
            "search.max_buckets",
            "byoc.jvm.heap_percent",
        }

    def test_watermarks_must_increase(self) -> None:
        with pytest.raises(ValidationFailed):
            es_settings.merge_config({}, {"cluster.routing.allocation.disk.watermark.high": "96%"})
        merged = es_settings.merge_config(
            {},
            {
                "cluster.routing.allocation.disk.watermark.flood_stage": "97%",
                "cluster.routing.allocation.disk.watermark.high": "96%",
            },
        )
        assert merged["cluster.routing.allocation.disk.watermark.high"] == "96%"

    def test_split_and_hash(self) -> None:
        dynamic, static = es_settings.split(
            {
                "search.max_buckets": 20000,
                "thread_pool.write.queue_size": 500,
                "action.destructive_requires_name": False,
            }
        )
        assert dynamic == {"action.destructive_requires_name": "false", "search.max_buckets": "20000"}
        assert static == {"thread_pool.write.queue_size": "500"}
        assert es_settings.settings_hash(dynamic) == es_settings.settings_hash(dict(reversed(dynamic.items())))
        assert es_settings.settings_hash({}) != es_settings.settings_hash(dynamic)

    def test_hash_matches_the_agent(self) -> None:
        # Same vector as agent/elasticsearch-agent/internal/configsync/configsync_test.go.
        assert es_settings.settings_hash({"search.max_buckets": "20000"}) == HASH_VECTOR

    def test_config_plan(self) -> None:
        plan = es.config_plan({}, {"search.max_buckets": 20000, "thread_pool.write.queue_size": 500})
        assert plan == {"dynamic": ["search.max_buckets"], "static": ["thread_pool.write.queue_size"]}

    def test_configure_renders_settings(self) -> None:
        s = ha_spec().with_config(
            {"search.max_buckets": 20000, "byoc.jvm.heap_percent": 40, "http.max_content_length": "200mb"}
        )
        settings = es.configure(s, es.provision(s, ZONES).nodes, None)
        assert settings["cluster_settings"] == {"search.max_buckets": "20000"}
        assert settings["node_settings"] == {"http.max_content_length": "200mb"}
        assert settings["heap_percent"] == 40
        assert settings["cluster_settings_hash"] == es_settings.settings_hash({"search.max_buckets": "20000"})

    def test_no_security_or_network_settings_offered(self) -> None:
        keys = {s.key for s in es_settings.CATALOG}
        assert not any(
            k.startswith(("xpack.", "network.", "discovery.", "path.", "transport.", "cluster.initial")) for k in keys
        )


HASH_VECTOR = "971a936de98cf3f8"  # the empty set hashes to 44136fa355b3678a


class TestAllowlistsAgree:
    """The same allowlists are compiled into the agent and enforced by the Terraform module."""

    ROOT = Path(__file__).resolve().parents[3]

    def test_agent_dynamic_keys(self) -> None:
        source = (self.ROOT / "agent/elasticsearch-agent/internal/configsync/configsync.go").read_text()
        block = source[source.index("var DynamicKeys = []string{") : source.index("}", source.index("var DynamicKeys"))]
        assert re.findall(r'"([^"]+)"', block) == list(es_settings.DYNAMIC_KEYS)

    def test_terraform_lists(self) -> None:
        source = (self.ROOT / "infrastructure/terraform/gcp/modules/elasticsearch/variables.tf").read_text()
        start = source.index('variable "cluster_settings"')
        dynamic = re.findall(r'"([a-z_.]+\.[a-z_.]+)"', source[start : source.index("error_message", start)])
        assert dynamic == list(es_settings.DYNAMIC_KEYS)
        start = source.index('variable "node_settings"')
        block = source[source.index("for prefix in [", start) : source.index("] :", start)]
        assert re.findall(r'"([^"]+)"', block) == list(es_settings.RESERVED_PREFIXES)

    def test_apply_config_reserved_list(self) -> None:
        source = (self.ROOT / "infrastructure/terraform/gcp/modules/elasticsearch/scripts/apply-config.sh").read_text()
        block = source[source.index("RESERVED=(") : source.index(")", source.index("RESERVED=("))]
        assert block.split("(", 1)[1].split() == list(es_settings.RESERVED_PREFIXES)


class TestCustomSettings:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(8192, "8192"), (False, "false"), (1.5, "1.5"), (["a:1", "b:2"], "a:1,b:2"), ("  x  ", "x")],
    )
    def test_values_are_normalized(self, value: Any, expected: str) -> None:
        assert es_settings.normalize_custom("indices.foo.bar", value) == expected

    @pytest.mark.parametrize("value", ["", "a\nb", "x" * 513, {"a": 1}, [["nested"]]])
    def test_invalid_values(self, value: Any) -> None:
        with pytest.raises(ValueError):
            es_settings.normalize_custom("indices.foo.bar", value)

    @pytest.mark.parametrize(
        ("key", "owned"),
        [
            ("xpack.security.enabled", True),
            ("xpack.ml.enabled", False),
            ("http.port", True),
            ("http.max_content_length", False),
            ("cluster.name", True),
            ("cluster.max_shards_per_node", False),
            ("node.attr.zone", True),
            ("node.store.allow_mmap", False),
            ("network.host", True),
            ("bootstrap.memory_lock", True),
        ],
    )
    def test_reserved(self, key: str, owned: bool) -> None:
        assert es_settings.reserved(key) is owned

    def test_custom_settings_are_static_and_limited(self) -> None:
        assert es.config_plan({}, {"indices.foo.bar": "1"}) == {"dynamic": [], "static": ["indices.foo.bar"]}
        too_many = {f"indices.custom.s{i}": "1" for i in range(es_settings.MAX_CUSTOM + 1)}
        with pytest.raises(ValidationFailed):
            es_settings.merge_config({}, too_many)
