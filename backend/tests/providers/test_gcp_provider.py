from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import Settings
from app.domain.errors import CloudProviderError, PlatformError, ProvisioningError, ValidationFailed
from app.domain.network import NetworkRef, SubnetRef
from app.providers.cloud.base import (
    CloudAccountContext,
    InfrastructureRequest,
    MachineType,
    NetworkLookup,
    NodePlacement,
    Region,
)
from app.providers.cloud.gcp.descriptor import GcpDescriptor
from app.providers.cloud.gcp.errors import from_google_response, from_terraform_diagnostics
from app.providers.cloud.gcp.permissions import REQUIRED_PERMISSIONS, role_for_permission
from app.providers.cloud.gcp.provider import GCPProvider, guard_plan, parse_outputs, resource_type, summarize_plan
from app.providers.cloud.gcp.terraform import (
    backend_config,
    module_variables,
    prepare_workspace,
    render_root_module,
    state_encryption_env,
)

ACCOUNT = CloudAccountContext(
    account_id="acct",
    organization_id="org",
    provider="gcp",
    project_id="customer-prod",
    auth_type="impersonation",
    service_account_email="db-platform-provisioner@customer-prod.iam.gserviceaccount.com",
    region="asia-south1",
)
PACKAGE = {
    "apt_repository": "https://artifacts.elastic.co/packages/9.x/apt",
    "signing_key_url": "https://artifacts.elastic.co/GPG-KEY-elasticsearch",
    "signing_key_fingerprint": "46095ACC8548582C1A2699A9D27D666CD88E42B4",
    "sha256": {"amd64": "a" * 64, "arm64": "b" * 64},
}


def request(**overrides: Any) -> InfrastructureRequest:
    values: dict[str, Any] = {
        "cluster_id": "0b8c1f7e-6c1e-4d7b-9d1c-2a0f4a7a3f9a",
        "organization_id": "org",
        "resource_prefix": "production-search-0b8c",
        "engine": "elasticsearch",
        "engine_version": "9.5.4",
        "project_id": "customer-prod",
        "region": "asia-south1",
        "zone": "asia-south1-a",
        "machine_type": "e2-standard-8",
        "storage_gb": 500,
        "storage_type": "pd-balanced",
        "high_availability": True,
        "nodes": [
            NodePlacement("node-1", 1, "asia-south1-a", ("master", "data", "ingest")),
            NodePlacement("node-2", 2, "asia-south1-b", ("master", "data", "ingest")),
        ],
        "engine_settings": {
            "cluster_name": "production-search",
            "version": "9.5.4",
            "package": PACKAGE,
            "seed_nodes": ["node-1", "node-2"],
            "initial_master_nodes": ["node-1", "node-2"],
            "zone_awareness": True,
        },
        "labels": {"managed-by": "byoc"},
    }
    values.update(overrides)
    return InfrastructureRequest(**values)


class TestErrorMapping:
    def test_missing_permission_matches_prd_example(self) -> None:
        body = {
            "error": {
                "code": 403,
                "message": "Required 'compute.instances.create' permission for 'projects/customer-prod/zones/x/instances/y'",
                "status": "PERMISSION_DENIED",
            }
        }
        err = from_google_response(403, body, project="customer-prod", call="instances.insert")
        assert err.code == "GCP_PERMISSION_DENIED"
        assert err.message == "GCP authorization failed."
        assert err.reason == "Service account does not have compute.instances.create permission."
        assert "roles/compute.instanceAdmin.v1" in (err.suggested_action or "")

    def test_disabled_api(self) -> None:
        body = {
            "error": {
                "code": 403,
                "message": "Compute Engine API has not been used in project 123 before or it is disabled.",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "SERVICE_DISABLED",
                        "metadata": {"service": "compute.googleapis.com"},
                    }
                ],
            }
        }
        err = from_google_response(403, body, project="customer-prod", call="projects.get")
        assert err.code == "GCP_API_DISABLED"
        assert "gcloud services enable compute.googleapis.com --project customer-prod" in (err.suggested_action or "")

    def test_quota_from_terraform(self) -> None:
        diagnostics = [
            {
                "severity": "error",
                "summary": "Error creating instance: googleapi: Error 403: Quota 'CPUS' exceeded.  Limit: 24.0 in region asia-south1.",
                "detail": "",
            }
        ]
        err = from_terraform_diagnostics(diagnostics, project="customer-prod", stage="apply")
        assert err.code == "GCP_QUOTA_EXCEEDED"
        assert "CPUS" in (err.reason or "") and "24.0" in (err.reason or "")

    def test_capacity(self) -> None:
        diags = [
            {
                "severity": "error",
                "summary": "ZONE_RESOURCE_POOL_EXHAUSTED: The zone does not have enough resources",
                "detail": "",
            }
        ]
        assert from_terraform_diagnostics(diags, project="p", stage="apply").code == "GCP_CAPACITY_UNAVAILABLE"

    def test_unknown_terraform_error_is_summarised_not_dumped(self) -> None:
        diags = [{"severity": "error", "summary": "Something odd", "detail": "x" * 5000}]
        err = from_terraform_diagnostics(diags, project="p", stage="plan")
        assert err.code == "TERRAFORM_FAILED"
        assert len(err.reason or "") <= 500

    def test_role_hints(self) -> None:
        assert role_for_permission("compute.firewalls.create") == "roles/compute.securityAdmin"
        assert role_for_permission("iam.serviceAccounts.actAs") == "roles/iam.serviceAccountUser"
        assert role_for_permission("secretmanager.secrets.create") == "roles/secretmanager.admin"


class TestPlanGuard:
    def plan(self, *changes: tuple[str, list[str]]) -> dict[str, Any]:
        return {"resource_changes": [{"address": a, "change": {"actions": acts}} for a, acts in changes]}

    def test_summary(self) -> None:
        summary = summarize_plan(
            self.plan(
                ("module.cluster.google_compute_network.this[0]", ["create"]),
                ('module.cluster.module.compute.google_compute_instance.node["node-1"]', ["update"]),
                ('module.cluster.module.compute.google_compute_disk.data["node-3"]', ["delete"]),
                ("module.cluster.random_password.elastic", ["no-op"]),
            )
        )
        assert (summary.to_add, summary.to_change, summary.to_destroy) == (1, 1, 1)
        assert summary.describe() == "Plan: 1 to add, 1 to change, 1 to destroy."

    def test_resource_type(self) -> None:
        assert (
            resource_type('module.cluster.module.compute.google_compute_instance.node["node-2"]')
            == "google_compute_instance"
        )

    def test_refuses_replacing_a_data_node(self) -> None:
        summary = summarize_plan(
            self.plan(('module.cluster.module.compute.google_compute_instance.node["node-1"]', ["delete", "create"]))
        )
        with pytest.raises(ProvisioningError) as excinfo:
            guard_plan(summary)
        assert excinfo.value.code == "UNSAFE_PLAN"

    def test_refuses_removing_any_node(self) -> None:
        # Scale-down is not part of the MVP, so no plan may remove a VM or its data disk.
        for address in (
            'module.cluster.module.compute.google_compute_instance.node["node-4"]',
            'module.cluster.module.storage.google_compute_disk.data["node-4"]',
        ):
            with pytest.raises(ProvisioningError):
                guard_plan(summarize_plan(self.plan((address, ["delete"]))))

    def test_non_stateful_changes_are_fine(self) -> None:
        guard_plan(
            summarize_plan(self.plan(("module.cluster.google_compute_firewall.internal", ["delete", "create"]))),
        )


class TestTerraformRendering:
    def test_root_module(self) -> None:
        req = request()
        variables = module_variables(req, agent=None, control_plane_url="", agent_audience="aud")
        root = render_root_module(req, variables, backend_config(req))
        module = root["module"]["cluster"]
        assert module["es_version"] == "9.5.4" and module["es_package"] == PACKAGE
        assert "es_major_version" not in module
        assert module["source"] == "./modules/elasticsearch"
        assert module["nodes"] == {
            "node-1": {"zone": "asia-south1-a", "ordinal": 1, "roles": ["master", "data", "ingest"]},
            "node-2": {"zone": "asia-south1-b", "ordinal": 2, "roles": ["master", "data", "ingest"]},
        }
        assert module["architecture"] == "x86_64"
        assert module["agent_binary_path"] == ""
        assert root["terraform"]["backend"] == {"local": {"path": "terraform.tfstate"}}
        assert root["output"]["nodes"]["value"] == "${module.cluster.nodes}"
        assert root["provider"]["google"]["project"] == "customer-prod"

    def test_dedicated_layout_and_configuration(self) -> None:
        req = request(
            nodes=[
                NodePlacement("master-1", 1, "asia-south1-a", ("master",), "master", "e2-standard-4", 20),
                NodePlacement("data-1", 2, "asia-south1-b", ("data", "ingest"), "data", "e2-standard-8", 500),
                NodePlacement("coord-1", 3, "asia-south1-c", (), "coordinating", "e2-standard-4", 20),
            ],
            load_balancer_nodes=["coord-1"],
        )
        req.engine_settings.update(
            {
                "layout": "dedicated",
                "forced_awareness_zones": ["asia-south1-a", "asia-south1-b", "asia-south1-c"],
                "cluster_settings": {"search.max_buckets": "20000"},
                "cluster_settings_hash": "971a936de98cf3f8",
                "node_settings": {"thread_pool.write.queue_size": "20000"},
                "heap_percent": 40,
                "node_generations": {"master-1": 0, "data-1": 2, "coord-1": 0},
            }
        )
        variables = module_variables(req, agent=None, control_plane_url="", agent_audience="a")
        assert variables["nodes"] == {
            "master-1": {
                "zone": "asia-south1-a", "ordinal": 1, "roles": ["master"],
                "machine_type": "e2-standard-4", "data_disk_size_gb": 20,
            },
            "data-1": {
                "zone": "asia-south1-b", "ordinal": 2, "roles": ["data", "ingest"],
                "machine_type": "e2-standard-8", "data_disk_size_gb": 500, "config_generation": 2,
            },
            "coord-1": {
                "zone": "asia-south1-c", "ordinal": 3, "roles": [],
                "machine_type": "e2-standard-4", "data_disk_size_gb": 20,
            },
        }  # fmt: skip
        assert variables["layout"] == "dedicated"
        assert variables["load_balancer_nodes"] == ["coord-1"]
        assert variables["cluster_settings"] == {"search.max_buckets": "20000"}
        assert variables["node_settings"] == {"thread_pool.write.queue_size": "20000"}
        assert variables["heap_percent"] == 40
        root = render_root_module(req, variables, backend_config(req))
        assert root["output"]["endpoint"]["value"] == "${module.cluster.endpoint}"

    def test_combined_layout_defaults(self) -> None:
        variables = module_variables(request(), agent=None, control_plane_url="", agent_audience="a")
        assert variables["layout"] == "combined" and variables["load_balancer_nodes"] == []
        assert variables["cluster_settings"] == {} and variables["heap_percent"] == 50

    def test_state_stays_local_until_the_platform_state_bucket_exists(self) -> None:
        # P5 moves state to the platform's bucket, one prefix per organization and cluster (ADR 0005).
        assert backend_config(request()) == {"local": {"path": "terraform.tfstate"}}

    def test_arm_machine(self) -> None:
        variables = module_variables(
            request(machine_type="t2a-standard-4"), agent=None, control_plane_url="", agent_audience="a"
        )
        assert variables["architecture"] == "arm64"

    def test_workspace_copies_modules(self, tmp_path: Path) -> None:
        modules = tmp_path / "modules" / "elasticsearch"
        modules.mkdir(parents=True)
        (modules / "main.tf").write_text("# module")
        req = request()
        workspace = prepare_workspace(tmp_path / "ws", tmp_path / "modules", req, {"module": {}})
        assert (workspace / "modules" / "elasticsearch" / "main.tf").exists()
        assert (workspace / "main.tf.json").exists()

    def test_state_encryption_config(self) -> None:
        config = state_encryption_env(b"k" * 32)["TF_ENCRYPTION"]
        assert 'key_provider "pbkdf2"' in config and "enforced = true" in config


class FakeClient:
    def __init__(self, granted: set[str] | None = None, disabled: set[str] | None = None) -> None:
        self.principal = ACCOUNT.service_account_email
        self.granted = set(REQUIRED_PERMISSIONS) if granted is None else granted
        self.disabled = disabled or set()

    def ensure_token(self) -> None:
        pass

    def get_project(self) -> dict[str, Any]:
        return {"lifecycleState": "ACTIVE"}

    def probe_service(self, service: str) -> None:
        if service in self.disabled:
            raise from_google_response(
                403,
                {
                    "error": {
                        "message": "x",
                        "details": [{"reason": "SERVICE_DISABLED", "metadata": {"service": service}}],
                    }
                },
                project="customer-prod",
                call="probe",
            )

    def test_permissions(self, permissions: Any) -> set[str]:
        return self.granted & set(permissions)

    def get_zone(self, zone: str) -> dict[str, Any] | None:
        return (
            {"region": "https://compute.googleapis.com/compute/v1/projects/p/regions/asia-south1", "status": "UP"}
            if zone.startswith("asia-south1")
            else None
        )

    def get_machine_type(self, zone: str, name: str) -> MachineType | None:
        return {
            "e2-standard-8": MachineType("e2-standard-8", 8, 32),
            "n4-standard-8": MachineType("n4-standard-8", 8, 32),
        }.get(name)

    def list_regions(self) -> list[Region]:
        return [Region("asia-south1", ("asia-south1-a", "asia-south1-b", "asia-south1-c"))]


def provider(client: FakeClient) -> GCPProvider:
    return GCPProvider(Settings(environment="test"), client_factory=lambda _account: client)  # type: ignore[arg-type,return-value]


class TestValidation:
    def test_all_good(self) -> None:
        result = provider(FakeClient()).validate_credentials(ACCOUNT)
        assert result.valid
        assert {c.key for c in result.checks} >= {"credentials", "project", "permissions", "api:compute.googleapis.com"}

    def test_missing_permissions_reported_prd_style(self) -> None:
        granted = set(REQUIRED_PERMISSIONS) - {"compute.instances.create", "compute.disks.create"}
        result = provider(FakeClient(granted=granted)).validate_credentials(ACCOUNT)
        assert not result.valid
        assert result.missing_permissions == ["compute.disks.create", "compute.instances.create"]
        assert result.error is not None and result.error["code"] == "GCP_PERMISSION_DENIED"
        assert result.error["details"]["missing_permissions"] == result.missing_permissions

    def test_disabled_api(self) -> None:
        result = provider(FakeClient(disabled={"secretmanager.googleapis.com"})).validate_credentials(ACCOUNT)
        assert not result.valid
        failed = [c for c in result.checks if c.status == "failed"]
        assert failed[0].key == "api:secretmanager.googleapis.com"

    def test_placement(self) -> None:
        gcp = provider(FakeClient())
        assert gcp.validate_placement(ACCOUNT, "asia-south1", "asia-south1-a", "e2-standard-8").vcpus == 8
        with pytest.raises(ValidationFailed):
            gcp.validate_placement(ACCOUNT, "asia-south1", "us-central1-a", "e2-standard-8")
        with pytest.raises(ValidationFailed):
            gcp.validate_placement(ACCOUNT, "asia-south1", "asia-south1-a", "e2-mega-1")
        with pytest.raises(ValidationFailed, match="not supported"):
            gcp.validate_placement(ACCOUNT, "asia-south1", "asia-south1-a", "n4-standard-8")

    def test_ha_zones(self) -> None:
        gcp = provider(FakeClient()).descriptor
        assert gcp.placement_zones("asia-south1", "asia-south1-b", spread=True) == [
            "asia-south1-b",
            "asia-south1-a",
            "asia-south1-c",
        ]
        assert gcp.placement_zones("asia-south1", "asia-south1-b", spread=False) == ["asia-south1-b"]


def test_parse_outputs() -> None:
    state = parse_outputs(
        {
            "nodes": {
                "node-1": {
                    "instance_name": "ps-node-1",
                    "instance_id": 123,
                    "zone": "asia-south1-a",
                    "private_ip": "10.10.0.2",
                    "hostname": "ps-node-1.asia-south1-a.c.p.internal",
                }
            },
            "network": "net",
        }
    )
    assert state.nodes[0].instance_id == "123"
    assert state.outputs == {"network": "net"}


def test_permission_names_are_well_formed() -> None:
    pattern = re.compile(r"^[a-z]+\.[a-zA-Z]+\.[a-zA-Z]+$")
    assert all(pattern.match(p) for p in REQUIRED_PERMISSIONS)
    assert len(set(REQUIRED_PERMISSIONS)) == len(REQUIRED_PERMISSIONS)


def test_unreachable_is_cloud_error() -> None:
    err = from_google_response(500, {"error": {"message": "backend error"}}, project="p", call="x")
    assert isinstance(err, CloudProviderError) and err.code == "GCP_API_ERROR"


def test_custom_role_matches_validator_permissions() -> None:
    """The onboarding module's custom role must grant exactly what validation checks for."""
    permissions_tf = (
        Path(__file__).resolve().parents[3] / "infrastructure/terraform/gcp/modules/control-plane-access/permissions.tf"
    ).read_text()
    in_terraform = set(re.findall(r'"([a-z]+\.[A-Za-z]+\.[A-Za-z]+)"', permissions_tf))
    assert in_terraform == set(REQUIRED_PERMISSIONS)


def test_mock_plan_addresses_exist_in_terraform_modules() -> None:
    """Simulated plans mirror real resource addresses, so the plan guard behaves the same."""
    from app.providers.cloud.mock.gcp import (
        CLUSTER_RESOURCES,
        DEDICATED_VPC_RESOURCES,
        NETWORK_RESOURCES,
        NODE_RESOURCES,
    )

    FOUNDATION_RESOURCES = CLUSTER_RESOURCES + NETWORK_RESOURCES + DEDICATED_VPC_RESOURCES

    modules = Path(__file__).resolve().parents[3] / "infrastructure/terraform/gcp/modules"
    sources: dict[str, str] = {}
    for tf in modules.glob("*/*.tf"):
        sources[tf.parent.name] = sources.get(tf.parent.name, "") + tf.read_text()
    for address in [a for a, _ in FOUNDATION_RESOURCES] + [a.format(node="node-1") for a in NODE_RESOURCES]:
        parts = re.sub(r"\[[^\]]*\]", "", address).split(".")
        # module.cluster.<type>.<name> lives in the stack module itself;
        # module.cluster.module.<child>.<type>.<name> in a child module.
        module = parts[3] if len(parts) == 6 else "elasticsearch"
        resource_type, name = parts[-2], parts[-1]
        assert f'resource "{resource_type}" "{name}"' in sources[module], address


# ---------------------------------------------------------------- networks (docs/adr/0013)

BASE = "https://www.googleapis.com/compute/v1/projects/customer-prod"
NETWORK_REF = NetworkRef(
    id="net-1",
    name="prod-network",
    vpc="projects/customer-prod/global/networks/prod-vpc",
    subnets=(SubnetRef("projects/customer-prod/regions/asia-south1/subnetworks/db-subnet", "10.20.16.0/20"),),
)


class NetworkClient(FakeClient):
    def __init__(self, subnet: dict[str, Any] | None = None, routers: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.subnet = subnet
        self.routers = routers or []

    def get_network(self, name: str) -> dict[str, Any] | None:
        return {"name": name, "selfLink": f"{BASE}/global/networks/{name}"} if name == "prod-vpc" else None

    def get_subnetwork(self, region: str, name: str) -> dict[str, Any] | None:
        return self.subnet

    def list_routers(self, region: str) -> list[dict[str, Any]]:
        return self.routers


def subnet(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "db-subnet",
        "network": f"{BASE}/global/networks/prod-vpc",
        "ipCidrRange": "10.20.16.0/20",
        "privateIpGoogleAccess": True,
        "purpose": "PRIVATE",
        "selfLink": f"{BASE}/regions/asia-south1/subnetworks/db-subnet",
        **overrides,
    }


LOOKUP = NetworkLookup(region="asia-south1", vpc="prod-vpc", subnets=("db-subnet",))


class TestNetworkLookup:
    def test_existing_network(self) -> None:
        nat = {
            "name": "r",
            "network": f"{BASE}/global/networks/prod-vpc",
            "nats": [
                {
                    "name": "nat",
                    "sourceSubnetworkIpRangesToNat": "LIST_OF_SUBNETWORKS",
                    "subnetworks": [{"name": f"{BASE}/regions/asia-south1/subnetworks/db-subnet"}],
                }
            ],
        }
        details = provider(NetworkClient(subnet(), [nat])).describe_network(ACCOUNT, LOOKUP)
        assert details.valid and details.warnings == []
        assert details.vpc == "projects/customer-prod/global/networks/prod-vpc"
        assert details.subnets[0].id == "projects/customer-prod/regions/asia-south1/subnetworks/db-subnet"
        assert details.subnets[0].available_ips == 4092 and details.subnets[0].zone is None
        assert details.zones == ["asia-south1-a", "asia-south1-b", "asia-south1-c"]

    def test_nat_for_other_subnets_does_not_count(self) -> None:
        other = {
            "name": "r",
            "network": f"{BASE}/global/networks/prod-vpc",
            "nats": [{"name": "nat", "sourceSubnetworkIpRangesToNat": "LIST_OF_SUBNETWORKS", "subnetworks": []}],
        }
        details = provider(NetworkClient(subnet(privateIpGoogleAccess=False), [other])).describe_network(
            ACCOUNT, LOOKUP
        )
        assert details.valid and len(details.warnings) == 2

    @pytest.mark.parametrize(
        ("body", "code"),
        [
            (None, "SUBNET_NOT_FOUND"),
            (subnet(network=f"{BASE}/global/networks/other"), "SUBNET_NOT_IN_VPC"),
            (subnet(purpose="PRIVATE_SERVICE_CONNECT"), "SUBNET_NOT_USABLE"),
            (subnet(stackType="IPV6_ONLY"), "SUBNET_NOT_USABLE"),
        ],
    )
    def test_unusable_subnets(self, body: dict[str, Any] | None, code: str) -> None:
        details = provider(NetworkClient(body)).describe_network(ACCOUNT, LOOKUP)
        assert not details.valid and details.error is not None and details.error["code"] == code

    def test_identifiers(self) -> None:
        gcp = provider(NetworkClient()).descriptor
        with pytest.raises(ValidationFailed):
            gcp.check_network_lookup(NetworkLookup("asia-south1", "prod-vpc", ("a", "b")))
        with pytest.raises(ValidationFailed):
            gcp.check_network_lookup(NetworkLookup("mars-1", "prod-vpc", ("a",)))
        assert gcp.check_network_lookup(NetworkLookup(" asia-south1", "prod-vpc ", ("db-subnet",))) == LOOKUP

    def test_terraform_uses_the_registered_network(self) -> None:
        variables = module_variables(request(network=NETWORK_REF), agent=None, control_plane_url="", agent_audience="a")
        assert variables["network"] == {
            "create": False,
            "subnet_cidr": "10.20.16.0/20",
            "existing_network": "projects/customer-prod/global/networks/prod-vpc",
            "existing_subnetwork": "projects/customer-prod/regions/asia-south1/subnetworks/db-subnet",
        }
        # Clusters created before registered networks keep the module's dedicated VPC.
        assert "network" not in module_variables(request(), agent=None, control_plane_url="", agent_audience="a")

    def test_provisioner_no_longer_creates_networks(self) -> None:
        for permission in ("compute.networks.create", "compute.subnetworks.delete", "compute.routers.create"):
            assert permission not in REQUIRED_PERMISSIONS
        assert {"compute.firewalls.create", "compute.subnetworks.use"} <= set(REQUIRED_PERMISSIONS)


class TestStaticPlacementCheck:
    """The API's request-time check (no cloud access): a registered network's zones, looked up in
    the customer's project, win over the static region list (regression: regions
    missing from the list blocked real clusters)."""

    def test_network_zones_are_authoritative(self, settings: Settings) -> None:
        GCP_DESCRIPTOR = GcpDescriptor(settings, simulated=False)  # noqa: N806

        zones = ["me-central2-a", "me-central2-b", "me-central2-c"]
        machine = GCP_DESCRIPTOR.validate_placement("me-central2", "me-central2-a", "e2-standard-4", zones)
        assert machine.name == "e2-standard-4"
        with pytest.raises(ValidationFailed) as excinfo:
            GCP_DESCRIPTOR.validate_placement("me-central2", "me-central2-x", "e2-standard-4", zones)
        assert "zone" in excinfo.value.details["fields"]

    def test_without_a_network_the_static_list_applies(self, settings: Settings) -> None:
        GCP_DESCRIPTOR = GcpDescriptor(settings, simulated=False)  # noqa: N806

        with pytest.raises(ValidationFailed):
            GCP_DESCRIPTOR.validate_placement("me-central2", "me-central2-a", "e2-standard-4")
        assert GCP_DESCRIPTOR.validate_placement("asia-east1", "asia-east1-a", "e2-standard-4").vcpus == 4


class _Response:
    def __init__(self, status: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status
        self._body = body or {}
        self.content = b"{}"
        self.text = "{}"

    def json(self) -> dict[str, Any]:
        return self._body


class _FlakySession:
    """Fails the first ``failures`` calls with ``error`` (an exception or a status code)."""

    def __init__(self, failures: int, error: Any) -> None:
        self.failures, self.error, self.calls = failures, error, 0

    def request(self, *args: Any, **kwargs: Any) -> _Response:
        self.calls += 1
        if self.calls <= self.failures:
            if isinstance(self.error, int):
                return _Response(self.error, {"error": {"message": "backend error"}})
            raise self.error
        return _Response(200, {"projectId": "p", "lifecycleState": "ACTIVE"})


def _client(session: _FlakySession) -> Any:
    from app.providers.cloud.gcp.client import GcpApiClient

    client = GcpApiClient.__new__(GcpApiClient)
    client.project, client.timeout, client._session = "p", 5.0, session
    client.sleep = lambda _: None  # type: ignore[method-assign]
    return client


class TestTransientFailures:
    """A DNS or network blip on the control plane must not fail a cluster operation."""

    def test_dns_failure_is_retried(self) -> None:
        import requests

        session = _FlakySession(2, requests.ConnectionError("Failed to resolve 'cloudresourcemanager.googleapis.com'"))
        assert _client(session).get_project()["projectId"] == "p"
        assert session.calls == 3

    def test_persistent_network_failure_is_reported(self) -> None:
        import requests

        session = _FlakySession(99, requests.ConnectionError("Failed to resolve 'compute.googleapis.com'"))
        with pytest.raises(CloudProviderError) as excinfo:
            _client(session).get_project()
        assert excinfo.value.code == "GCP_UNREACHABLE" and session.calls == 5

    def test_transient_google_errors_are_retried(self) -> None:
        session = _FlakySession(1, 503)
        assert _client(session).get_project()["projectId"] == "p"

    def test_permanent_errors_are_not_retried(self) -> None:
        session = _FlakySession(99, 403)
        with pytest.raises(PlatformError):
            _client(session).get_project()
        assert session.calls == 1
