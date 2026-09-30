from __future__ import annotations

import uuid

import pytest

from app.domain.cluster_spec import ClusterSpec, EnvironmentRef, validate_generic
from app.domain.enums import OperationType, Role
from app.domain.errors import Conflict, ValidationFailed
from app.domain.health import HealthThresholds, NodeObservation, evaluate_platform_node
from app.domain.network import NetworkRef, SubnetRef, plan_zones
from app.domain.rbac import Permission, can_change_membership, has_permission, permission_for_operation
from app.domain.reports import NodeReport
from app.domain.states import (
    AgentStatus,
    ClusterHealth,
    ClusterLifecycle,
    NodeHealth,
    OperationStatus,
    assert_cluster_transition,
    assert_operation_transition,
    can_transition_cluster,
    can_transition_operation,
    lifecycle_change,
    worst_cluster_health,
    worst_node_health,
)


def spec(**overrides: object) -> ClusterSpec:
    values = {
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
    return ClusterSpec(**values)  # type: ignore[arg-type]


class TestRbac:
    def test_every_role_can_read(self) -> None:
        for role in Role:
            for permission in (Permission.CLUSTER_READ, Permission.OPERATION_READ, Permission.AUDIT_READ):
                assert has_permission(role, permission)

    def test_viewer_is_read_only(self) -> None:
        writes = [p for p in Permission if not p.value.endswith(":read")]
        assert not any(has_permission(Role.VIEWER, p) for p in writes)

    def test_operator(self) -> None:
        for allowed in (
            Permission.CLUSTER_CREATE,
            Permission.CLUSTER_SCALE,
            Permission.CLUSTER_HEALTH_CHECK,
            Permission.CLUSTER_OPERATE,
            Permission.OPERATION_MANAGE,
        ):
            assert has_permission(Role.OPERATOR, allowed)
        for denied in (Permission.CLUSTER_DELETE, Permission.CLOUD_ACCOUNT_MANAGE, Permission.MEMBER_MANAGE):
            assert not has_permission(Role.OPERATOR, denied)

    def test_only_owner_and_admin_delete(self) -> None:
        assert [r for r in Role if has_permission(r, Permission.CLUSTER_DELETE)] == [Role.OWNER, Role.ADMIN]

    def test_operation_permissions(self) -> None:
        assert permission_for_operation(OperationType.SCALE_CLUSTER) == Permission.CLUSTER_SCALE
        assert permission_for_operation(OperationType.DELETE_CLUSTER) == Permission.CLUSTER_DELETE
        # Reserved operation types need the most restrictive cluster permission.
        assert permission_for_operation(OperationType.RESTORE_CLUSTER) == Permission.CLUSTER_DELETE

    def test_only_owners_touch_owners(self) -> None:
        assert can_change_membership(Role.OWNER, Role.OWNER, Role.VIEWER)
        assert can_change_membership(Role.ADMIN, Role.OPERATOR, Role.ADMIN)
        assert not can_change_membership(Role.ADMIN, None, Role.OWNER)
        assert not can_change_membership(Role.ADMIN, Role.OWNER, Role.ADMIN)
        assert not can_change_membership(Role.ADMIN, Role.OWNER, None)
        assert not can_change_membership(Role.OPERATOR, Role.VIEWER, Role.OPERATOR)


class TestOperationStates:
    def test_happy_path(self) -> None:
        path = [
            OperationStatus.PENDING,
            OperationStatus.VALIDATING,
            OperationStatus.PROVISIONING,
            OperationStatus.BOOTSTRAPPING,
            OperationStatus.CONFIGURING,
            OperationStatus.HEALTH_CHECK,
            OperationStatus.COMPLETED,
        ]
        for current, new in zip(path, path[1:], strict=False):
            assert can_transition_operation(current, new), (current, new)

    def test_states_only_move_forward(self) -> None:
        assert not can_transition_operation(OperationStatus.CONFIGURING, OperationStatus.PROVISIONING)
        assert not can_transition_operation(OperationStatus.HEALTH_CHECK, OperationStatus.BOOTSTRAPPING)

    def test_terminal_states_are_final(self) -> None:
        for terminal in (OperationStatus.COMPLETED, OperationStatus.FAILED, OperationStatus.CANCELLED):
            with pytest.raises(Conflict):
                assert_operation_transition(terminal, OperationStatus.PENDING)

    def test_cannot_skip_claiming(self) -> None:
        assert not can_transition_operation(OperationStatus.PENDING, OperationStatus.PROVISIONING)

    def test_abandoned_operation_can_be_requeued(self) -> None:
        assert can_transition_operation(OperationStatus.BOOTSTRAPPING, OperationStatus.PENDING)


class TestClusterLifecycle:
    def test_transitions(self) -> None:
        assert can_transition_cluster(ClusterLifecycle.CREATING, ClusterLifecycle.ACTIVE)
        assert can_transition_cluster(ClusterLifecycle.SCALING, ClusterLifecycle.ACTIVE)
        assert can_transition_cluster(ClusterLifecycle.FAILED, ClusterLifecycle.DELETING)
        for state in ClusterLifecycle:
            if state != ClusterLifecycle.DELETED:
                assert can_transition_cluster(state, ClusterLifecycle.DELETING), state
        assert not can_transition_cluster(ClusterLifecycle.DELETED, ClusterLifecycle.ACTIVE)
        assert not can_transition_cluster(ClusterLifecycle.DELETING, ClusterLifecycle.ACTIVE)
        with pytest.raises(Conflict):
            assert_cluster_transition(ClusterLifecycle.DELETING, ClusterLifecycle.SCALING)

    def test_an_operation_only_leaves_its_own_state(self) -> None:
        assert lifecycle_change("SCALING", ClusterLifecycle.SCALING, ClusterLifecycle.ACTIVE) == ClusterLifecycle.ACTIVE
        # A scale finishing after a delete was requested must not overwrite DELETING.
        assert lifecycle_change("DELETING", ClusterLifecycle.SCALING, ClusterLifecycle.ACTIVE) is None
        assert lifecycle_change("DELETING", ClusterLifecycle.CREATING, ClusterLifecycle.FAILED) is None

    def test_health_order(self) -> None:
        assert worst_cluster_health([ClusterHealth.HEALTHY, ClusterHealth.UNKNOWN]) == ClusterHealth.UNKNOWN
        assert worst_cluster_health([ClusterHealth.UNKNOWN, ClusterHealth.DEGRADED]) == ClusterHealth.DEGRADED
        assert worst_cluster_health([ClusterHealth.DEGRADED, ClusterHealth.UNHEALTHY]) == ClusterHealth.UNHEALTHY
        assert worst_cluster_health([]) == ClusterHealth.UNKNOWN
        assert worst_node_health([NodeHealth.HEALTHY, NodeHealth.UNHEALTHY]) == NodeHealth.UNHEALTHY
        assert {h.value for h in NodeHealth} == {"UNKNOWN", "HEALTHY", "UNHEALTHY"}


class TestClusterSpec:
    def test_desired_state_round_trip(self) -> None:
        original = spec()
        document = original.to_desired_state(generation=4)
        assert document["engine"] == {"type": "elasticsearch", "version": "9.5.4"}
        assert document["nodes"] == {"count": 3}
        assert document["storage"] == {"sizeGB": 500, "type": "pd-balanced"}
        assert document["highAvailability"] == {"enabled": True}
        assert document["generation"] == 4
        assert ClusterSpec.from_desired_state(document) == original

    def test_malformed_desired_state(self) -> None:
        with pytest.raises(ValidationFailed):
            ClusterSpec.from_desired_state({"cluster": {}})

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("name", "Production"),
            ("name", "a"),
            ("name", "ends-with-"),
            ("zone", "us-central1-a"),
            ("region", "Mumbai 1"),
            ("node_count", 0),
            ("storage_gb", 5),
        ],
    )
    def test_generic_validation(self, field: str, value: object) -> None:
        with pytest.raises(ValidationFailed) as excinfo:
            validate_generic(spec(**{field: value}))
        assert field in excinfo.value.details["fields"]

    def test_valid_spec_passes(self) -> None:
        validate_generic(spec())


def report(**system: float) -> NodeReport:
    return NodeReport.model_validate({"node_name": "node-1", "system": system, "engine": {}})


class TestPlatformNodeHealth:
    thresholds = HealthThresholds(agent_stale_seconds=90)

    def test_vm_down(self) -> None:
        obs = NodeObservation("node-2", 2, instance_status="TERMINATED", report=report(), report_age_seconds=5)
        node = evaluate_platform_node(obs, self.thresholds)
        assert (node.state, node.infrastructure, node.engine) == (
            NodeHealth.UNHEALTHY,
            NodeHealth.UNHEALTHY,
            NodeHealth.UNHEALTHY,
        )
        assert "VM unavailable" in node.reasons[0]

    def test_vm_missing(self) -> None:
        node = evaluate_platform_node(NodeObservation("node-2", 2, instance_status="NOT_FOUND"), self.thresholds)
        assert node.state == NodeHealth.UNHEALTHY and "missing" in node.reasons[0]

    def test_stale_agent_on_a_running_vm_is_unknown_not_dead(self) -> None:
        obs = NodeObservation("node-1", 1, instance_status="RUNNING", report=report(), report_age_seconds=300)
        node = evaluate_platform_node(obs, self.thresholds)
        assert (node.state, node.infrastructure, node.engine) == (
            NodeHealth.UNKNOWN,
            NodeHealth.HEALTHY,
            NodeHealth.UNKNOWN,
        )
        assert node.agent_status == AgentStatus.STALE
        assert "Agent unavailable" in node.reasons[0]

    def test_no_report_yet(self) -> None:
        node = evaluate_platform_node(NodeObservation("node-1", 1, instance_status="RUNNING"), self.thresholds)
        assert node.state == NodeHealth.UNKNOWN and node.agent_status == AgentStatus.NOT_REPORTED

    @pytest.mark.parametrize(
        ("disk", "infrastructure", "warnings"),
        [(50.0, NodeHealth.HEALTHY, 0), (85.0, NodeHealth.HEALTHY, 1), (93.0, NodeHealth.UNHEALTHY, 0)],
    )
    def test_disk_thresholds(self, disk: float, infrastructure: NodeHealth, warnings: int) -> None:
        obs = NodeObservation(
            "node-1", 1, instance_status="RUNNING", report=report(disk_percent=disk), report_age_seconds=1
        )
        node = evaluate_platform_node(obs, self.thresholds)
        assert node.infrastructure == infrastructure and len(node.warnings) == warnings
        assert node.agent_status == AgentStatus.REPORTING


class TestZonePlanning:
    ZONES = ["ap-south-1a", "ap-south-1b", "ap-south-1c"]

    def test_single_zone(self) -> None:
        assert plan_zones(self.ZONES, None, False) == ["ap-south-1a"]
        assert plan_zones(self.ZONES, "ap-south-1c", False) == ["ap-south-1c"]

    def test_ha_puts_the_preferred_zone_first(self) -> None:
        assert plan_zones(self.ZONES, "ap-south-1b", True) == ["ap-south-1b", "ap-south-1a", "ap-south-1c"]
        assert plan_zones([*self.ZONES, "ap-south-1d"], None, True) == ["ap-south-1a", "ap-south-1b", "ap-south-1c"]

    @pytest.mark.parametrize(
        ("zones", "preferred", "ha", "field"),
        [
            (["ap-south-1a", "ap-south-1b"], None, True, "high_availability"),
            (["ap-south-1a"], "ap-south-1b", False, "zone"),
            ([], None, False, "network_id"),
        ],
    )
    def test_refusals(self, zones: list[str], preferred: str | None, ha: bool, field: str) -> None:
        with pytest.raises(ValidationFailed) as excinfo:
            plan_zones(zones, preferred, ha)
        assert field in excinfo.value.details["fields"]

    def test_spec_round_trip_with_network(self) -> None:
        network = NetworkRef(
            id="n",
            name="aws-private",
            vpc="vpc-0a1b2c3d",
            subnets=(
                SubnetRef("subnet-0a", "10.1.0.0/21", "ap-south-1a"),
                SubnetRef("subnet-0b", "10.1.8.0/21", "ap-south-1b"),
            ),
        )
        full = spec(
            region="ap-south-1",
            zone="ap-south-1a",
            environment=EnvironmentRef("e", "test", "TEST"),
            network=network,
            zones=("ap-south-1a", "ap-south-1b"),
        )
        validate_generic(full)
        again = ClusterSpec.from_desired_state(full.with_node_count(5).to_desired_state(2))
        assert again.network == network and again.environment == full.environment
        assert again.zones == ("ap-south-1a", "ap-south-1b") and again.node_count == 5
        assert network.subnet_for("ap-south-1b").id == "subnet-0b"
        with pytest.raises(ValueError):
            network.subnet_for("ap-south-1c")

    def test_older_specs_have_no_network(self) -> None:
        doc = spec().to_desired_state(1)
        assert "network" not in doc and "environment" not in doc and "zones" not in doc["cloud"]
        assert ClusterSpec.from_desired_state(doc).network is None
