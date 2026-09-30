from __future__ import annotations

import pytest

from app.application.platform import Platform
from app.domain.errors import AuthenticationFailed
from app.providers.cloud.base import CloudAccountContext, NodeRef
from app.providers.cloud.mock.gcp import MockGcpProvider


def account(project: str) -> CloudAccountContext:
    return CloudAccountContext(
        account_id="a",
        organization_id="o",
        provider="gcp",
        project_id=project,
        auth_type="impersonation",
        service_account_email=f"db-platform-provisioner@{project}.iam.gserviceaccount.com",
    )


@pytest.fixture
def mock_cloud(cloud_platform: Platform) -> MockGcpProvider:
    cloud = cloud_platform.registry.cloud("gcp")
    assert isinstance(cloud, MockGcpProvider)
    return cloud


def test_valid_project(mock_cloud: MockGcpProvider) -> None:
    result = mock_cloud.validate_credentials(account("acme-prod"))
    assert result.valid
    assert result.checks[0].message.startswith("Impersonating db-platform-provisioner@acme-prod")


@pytest.mark.parametrize(
    ("project", "code"),
    [
        ("acme-denied", "GCP_PERMISSION_DENIED"),
        ("acme-disabled", "GCP_API_DISABLED"),
        ("acme-notfound", "GCP_PROJECT_NOT_FOUND"),
    ],
)
def test_failure_triggers(mock_cloud: MockGcpProvider, project: str, code: str) -> None:
    result = mock_cloud.validate_credentials(account(project))
    assert not result.valid
    assert result.error is not None and result.error["code"] == code


def test_dataplane_bootstrap_and_faults(mock_cloud: MockGcpProvider) -> None:
    dataplane = mock_cloud.dataplane
    cluster_id = "5f6c7a3e-0d5c-4b43-a54a-2e9f1d8f5b10"
    for i, zone in enumerate(("asia-south1-a", "asia-south1-b", "asia-south1-c"), start=1):
        dataplane.create_instance(
            cluster_id=cluster_id,
            project_id="acme-prod",
            zone=zone,
            name=f"c-node-{i}",
            node_name=f"node-{i}",
            version="9.5.4",
            labels={"cluster_name": "c", "storage_gb": 100, "memory_gb": 16},
        )
    refs = [
        NodeRef(f"node-{i}", f"c-node-{i}", z)
        for i, z in enumerate(("asia-south1-a", "asia-south1-b", "asia-south1-c"), 1)
    ]
    reports = dataplane.reports("acme-prod", refs)
    assert all(r.bootstrap and r.bootstrap["status"] == "ready" for r in reports.values())
    engine = reports["node-1"].report["engine"]  # type: ignore[index]
    assert engine["cluster_status"] == "green" and engine["number_of_nodes"] == 3 and engine["is_master"]

    dataplane.set_fault(cluster_id, "node-2", "vm_down")
    assert dataplane.statuses("acme-prod", refs)["node-2"] == "TERMINATED"
    reports = dataplane.reports("acme-prod", refs)
    assert reports["node-2"].report is None
    assert reports["node-1"].report["engine"]["cluster_status"] == "yellow"  # type: ignore[index]
    assert reports["node-1"].report["engine"]["number_of_nodes"] == 2  # type: ignore[index]

    dataplane.set_fault(cluster_id, "node-3", "vm_down")
    engine = dataplane.reports("acme-prod", refs)["node-1"].report["engine"]  # type: ignore[index]
    assert engine["cluster_status"] is None and "master_not_discovered" in engine["error"]

    dataplane.set_fault(cluster_id, "node-2", "clear")
    dataplane.set_fault(cluster_id, "node-3", "clear")
    dataplane.set_fault(cluster_id, "node-1", "disk_pressure")
    report = dataplane.reports("acme-prod", refs)["node-1"].report
    assert report is not None and report["system"]["disk_percent"] > 90
    assert dataplane.statuses("acme-prod", [NodeRef("node-9", "missing", "asia-south1-a")]) == {"node-9": "NOT_FOUND"}


def test_plan_refuses_to_remove_nodes(mock_cloud: MockGcpProvider) -> None:
    from app.domain.cluster_spec import ClusterSpec
    from app.domain.errors import ProvisioningError
    from app.providers.cloud.base import InfrastructureRequest
    from app.providers.database.elasticsearch import ElasticsearchProvider

    cluster_id = "7a1b2c3d-0000-4000-8000-000000000009"
    for i in (1, 2):
        mock_cloud.dataplane.create_instance(
            cluster_id=cluster_id,
            project_id="acme-prod",
            zone="asia-south1-a",
            name=f"p-node-{i}",
            node_name=f"node-{i}",
            version="9.5.4",
            labels={},
        )
    spec = ClusterSpec(
        name="p",
        engine="elasticsearch",
        version="9.5.4",
        cloud_provider="gcp",
        cloud_account_id="a",
        project_id="acme-prod",
        region="asia-south1",
        zone="asia-south1-a",
        machine_type="e2-standard-8",
        node_count=1,
        storage_gb=100,
        storage_type="pd-balanced",
        high_availability=False,
    )
    plan = ElasticsearchProvider().provision(spec, ["asia-south1-a"])
    request = InfrastructureRequest(
        cluster_id=cluster_id,
        organization_id="o",
        resource_prefix="p",
        engine="elasticsearch",
        engine_version="9.5.4",
        project_id="acme-prod",
        region="asia-south1",
        zone="asia-south1-a",
        machine_type="e2-standard-8",
        storage_gb=100,
        storage_type="pd-balanced",
        high_availability=False,
        nodes=plan.nodes,
        engine_settings=plan.settings,
        labels={},
    )

    class Progress:
        def message(self, text: str) -> None: ...
        def heartbeat(self) -> None: ...
        def check_cancelled(self) -> None: ...
        def sleep(self, seconds: float) -> None: ...

    with pytest.raises(ProvisioningError) as excinfo:
        mock_cloud.plan_infrastructure(account("acme-prod"), request, Progress())
    assert excinfo.value.code == "UNSAFE_PLAN" and "node-2" in (excinfo.value.reason or "")


def test_identity_tokens(mock_cloud: MockGcpProvider) -> None:
    descriptor = mock_cloud.descriptor
    token = descriptor.mint_identity_token(
        project_id="acme-prod", zone="asia-south1-a", instance_name="c-node-1", instance_id="42", audience="aud"
    )
    identity = descriptor.verify_instance_identity("acme-prod", token, "aud")
    assert (identity.instance_name, identity.instance_id) == ("c-node-1", "42")
    with pytest.raises(AuthenticationFailed):
        descriptor.verify_instance_identity("other-project", token, "aud")
    with pytest.raises(AuthenticationFailed):
        descriptor.verify_instance_identity("acme-prod", token, "wrong-audience")
