"""Environments, registered networks, AWS accounts and placement (docs/adr/0013, docs/adr/0014)."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.providers.cloud.mock.gcp import MockGcpProvider
from tests.conftest import (
    AWS_SUBNETS,
    account_request,
    aws_account_request,
    cluster_request,
    create_environment,
    delete_cluster,
    register_network,
)

RunWorker = Callable[[], list[str]]
Login = Callable[[str], dict[str, str]]


def audit_actions(client: TestClient, headers: dict[str, str], resource_id: str) -> list[str]:
    items = client.get(f"/api/v1/audit-logs?resource_id={resource_id}&limit=100", headers=headers).json()["items"]
    return [e["action"] for e in reversed(items)]


def aws_setup(client: TestClient, headers: dict[str, str], subnets: list[str] | None = None) -> dict[str, Any]:
    """An AWS account, a test environment and a network with subnets in three availability zones."""
    account = client.post("/api/v1/cloud-accounts", json=aws_account_request(), headers=headers)
    assert account.status_code == 201, account.text
    assert account.json()["status"] == "CONNECTED", account.json()
    environment = create_environment(client, headers, "test", "TEST")
    return register_network(
        client,
        headers,
        environment["id"],
        account.json()["id"],
        name="aws-private",
        region="ap-south-1",
        vpc="vpc-0a1b2c3d4e5f67890",
        subnets=subnets or AWS_SUBNETS,
    )


class TestEnvironments:
    def test_create_list_and_delete(self, client: TestClient, owner: dict[str, str]) -> None:
        created = create_environment(client, owner, "test-eu", "test")
        assert (created["type"], created["network_count"], created["cluster_count"]) == ("TEST", 0, 0)
        assert [e["name"] for e in client.get("/api/v1/environments", headers=owner).json()] == ["test-eu"]
        assert client.delete(f"/api/v1/environments/{created['id']}", headers=owner).status_code == 204
        assert client.get("/api/v1/environments", headers=owner).json() == []
        assert audit_actions(client, owner, created["id"]) == ["ENVIRONMENT_CREATED", "ENVIRONMENT_DELETED"]

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"name": "Production", "type": "PRODUCTION"}, "name"),
            ({"name": "prod env", "type": "PRODUCTION"}, "name"),
            ({"name": "staging", "type": "STAGING"}, "type"),
        ],
    )
    def test_validation(self, client: TestClient, owner: dict[str, str], body: dict[str, str], field: str) -> None:
        response = client.post("/api/v1/environments", json=body, headers=owner)
        assert response.status_code == 422 and field in response.json()["error"]["details"]["fields"]

    def test_names_are_unique(self, client: TestClient, owner: dict[str, str], environment: dict[str, Any]) -> None:
        response = client.post("/api/v1/environments", json={"name": "production", "type": "TEST"}, headers=owner)
        assert response.status_code == 409

    def test_cannot_delete_while_in_use(
        self, client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any], run_worker: RunWorker
    ) -> None:
        environment_id = running_cluster["environment"]["id"]
        blocked = client.delete(f"/api/v1/environments/{environment_id}", headers=owner)
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "ENVIRONMENT_NOT_EMPTY"
        environment = client.get(f"/api/v1/environments/{environment_id}", headers=owner).json()
        assert (environment["cluster_count"], environment["network_count"], environment["providers"]) == (
            1,
            1,
            ["gcp"],
        )
        # Delete the cluster, then the network: the environment can go, the deleted cluster keeps its history.
        delete_cluster(client, owner, running_cluster)
        run_worker()
        network_id = running_cluster["network"]["id"]
        assert client.delete(f"/api/v1/networks/{network_id}", headers=owner).status_code == 204
        assert client.delete(f"/api/v1/environments/{environment_id}", headers=owner).status_code == 204
        history = client.get("/api/v1/clusters?include_deleted=true", headers=owner).json()
        assert history[0]["environment"]["name"] == "production" and history[0]["network"]["name"] == "prod-network"

    def test_clusters_filter_by_environment(
        self, client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any]
    ) -> None:
        other = create_environment(client, owner, "test", "TEST")
        environment_id = running_cluster["environment"]["id"]
        assert len(client.get(f"/api/v1/clusters?environment_id={environment_id}", headers=owner).json()) == 1
        assert client.get(f"/api/v1/clusters?environment_id={other['id']}", headers=owner).json() == []


class TestGcpNetworks:
    def test_details_come_from_the_cloud(
        self, client: TestClient, owner: dict[str, str], network: dict[str, Any]
    ) -> None:
        assert network["status"] == "AVAILABLE" and network["provider"] == "gcp"
        assert (network["vpc"], network["subnets"], network["region"]) == ("prod-vpc", ["db-subnet"], "asia-south1")
        details = network["details"]
        assert details["vpc"] == "projects/acme-prod/global/networks/prod-vpc"
        (subnet,) = details["subnets"]
        assert subnet["id"] == "projects/acme-prod/regions/asia-south1/subnetworks/db-subnet"
        assert subnet["zone"] is None and ipaddress.ip_network(subnet["cidr"]).prefixlen == 20
        assert subnet["available_ips"] == 4092
        assert network["zones"] == ["asia-south1-a", "asia-south1-b", "asia-south1-c"]
        assert details["warnings"] == []
        assert {c["key"]: c["status"] for c in details["checks"]} == {
            "vpc": "passed",
            "subnet": "passed",
            "egress": "passed",
            "google_access": "passed",
        }
        assert audit_actions(client, owner, network["id"]) == ["NETWORK_CREATED", "NETWORK_VALIDATED"]

    def test_lookup_previews_without_registering(
        self, client: TestClient, owner: dict[str, str], cloud_account: dict[str, Any]
    ) -> None:
        body = {"cloud_account_id": cloud_account["id"], "region": "asia-south1", "vpc": "prod-vpc", "subnets": ["x1"]}
        details = client.post("/api/v1/networks/lookup", json=body, headers=owner).json()
        assert details["valid"] is True and details["subnets"][0]["name"] == "x1"
        assert client.get("/api/v1/environments", headers=owner).json() == []

    @pytest.mark.parametrize(
        ("vpc", "subnet", "code"),
        [
            ("notfound-vpc", "db-subnet", "NETWORK_NOT_FOUND"),
            ("prod-vpc", "notfound-subnet", "SUBNET_NOT_FOUND"),
            ("prod-vpc", "othervpc-subnet", "SUBNET_NOT_IN_VPC"),
            ("prod-vpc", "proxy-subnet", "SUBNET_NOT_USABLE"),
        ],
    )
    def test_problems_fail_the_network(
        self,
        client: TestClient,
        owner: dict[str, str],
        cloud_account: dict[str, Any],
        environment: dict[str, Any],
        vpc: str,
        subnet: str,
        code: str,
    ) -> None:
        network = register_network(client, owner, environment["id"], cloud_account["id"], vpc=vpc, subnets=[subnet])
        assert network["status"] == "FAILED" and network["zones"] == []
        assert network["details"]["error"]["code"] == code

    def test_missing_egress_is_a_warning(
        self, client: TestClient, owner: dict[str, str], cloud_account: dict[str, Any], environment: dict[str, Any]
    ) -> None:
        network = register_network(
            client, owner, environment["id"], cloud_account["id"], vpc="nonat-vpc", subnets=["nopga-subnet"]
        )
        assert network["status"] == "AVAILABLE"
        checks = {c["key"]: c["status"] for c in network["details"]["checks"]}
        assert checks["egress"] == checks["google_access"] == "warning"
        assert len(network["details"]["warnings"]) == 2

    @pytest.mark.parametrize(
        ("overrides", "field"),
        [
            ({"subnets": ["a-subnet", "b-subnet"]}, "subnets"),
            ({"vpc": "Prod_VPC"}, "vpc"),
            ({"region": "ap-south-1"}, "region"),
            ({"name": "Bad Name"}, "name"),
        ],
    )
    def test_identifiers_are_checked(
        self,
        client: TestClient,
        owner: dict[str, str],
        cloud_account: dict[str, Any],
        environment: dict[str, Any],
        overrides: dict[str, Any],
        field: str,
    ) -> None:
        body = {
            "name": "net",
            "cloud_account_id": cloud_account["id"],
            "region": "asia-south1",
            "vpc": "prod-vpc",
            "subnets": ["db-subnet"],
            **overrides,
        }
        response = client.post(f"/api/v1/environments/{environment['id']}/networks", json=body, headers=owner)
        assert response.status_code == 422 and field in response.json()["error"]["details"]["fields"]

    def test_needs_a_connected_account(
        self, client: TestClient, owner: dict[str, str], environment: dict[str, Any]
    ) -> None:
        failed = client.post("/api/v1/cloud-accounts", json=account_request("acme-denied"), headers=owner).json()
        body = {
            "name": "net-a",
            "cloud_account_id": failed["id"],
            "region": "asia-south1",
            "vpc": "v",
            "subnets": ["s"],
        }
        response = client.post(f"/api/v1/environments/{environment['id']}/networks", json=body, headers=owner)
        assert response.status_code == 422 and "cloud_account_id" in response.json()["error"]["details"]["fields"]

    def test_duplicates_are_refused(
        self, client: TestClient, owner: dict[str, str], cloud_account: dict[str, Any], network: dict[str, Any]
    ) -> None:
        body = {
            "name": "again",
            "cloud_account_id": cloud_account["id"],
            "region": "asia-south1",
            "vpc": "prod-vpc",
            "subnets": ["db-subnet"],
        }
        response = client.post(f"/api/v1/environments/{network['environment_id']}/networks", json=body, headers=owner)
        assert response.status_code == 409 and "prod-network" in response.json()["error"]["message"]

    def test_revalidation_marks_an_available_network_unavailable(
        self, client: TestClient, owner: dict[str, str], network: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = MockGcpProvider.describe_network

        def gone(self: MockGcpProvider, account: Any, lookup: Any) -> Any:
            return original(self, account, type(lookup)(lookup.region, "notfound-" + lookup.vpc, lookup.subnets))

        monkeypatch.setattr(MockGcpProvider, "describe_network", gone)
        after = client.post(f"/api/v1/networks/{network['id']}/validate", headers=owner).json()
        assert after["status"] == "UNAVAILABLE" and after["last_available_at"]
        response = client.post("/api/v1/clusters", json=cluster_request(network), headers=owner)
        assert response.status_code == 422 and response.json()["error"]["code"] == "NETWORK_NOT_AVAILABLE"

    def test_network_in_use_cannot_be_removed_and_holds_the_account(
        self, client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any], run_worker: RunWorker
    ) -> None:
        network_id = running_cluster["network"]["id"]
        blocked = client.delete(f"/api/v1/networks/{network_id}", headers=owner)
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "NETWORK_IN_USE"
        delete_cluster(client, owner, running_cluster)
        run_worker()
        account = client.delete(f"/api/v1/cloud-accounts/{running_cluster['cloud_account_id']}", headers=owner)
        assert account.status_code == 409 and "registered network" in account.json()["error"]["message"]
        assert client.delete(f"/api/v1/networks/{network_id}", headers=owner).status_code == 204
        assert client.delete(
            f"/api/v1/cloud-accounts/{running_cluster['cloud_account_id']}", headers=owner
        ).status_code == (204)

    def test_scale_needs_the_network_available(
        self,
        client: TestClient,
        owner: dict[str, str],
        running_cluster: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original = MockGcpProvider.describe_network

        def gone(self: MockGcpProvider, account: Any, lookup: Any) -> Any:
            return original(self, account, type(lookup)(lookup.region, "notfound", lookup.subnets))

        monkeypatch.setattr(MockGcpProvider, "describe_network", gone)
        network_id = running_cluster["network"]["id"]
        client.post(f"/api/v1/networks/{network_id}/validate", headers=owner)
        scale = client.post(f"/api/v1/clusters/{running_cluster['id']}/scale", json={"node_count": 5}, headers=owner)
        assert scale.status_code == 409 and scale.json()["error"]["code"] == "NETWORK_NOT_AVAILABLE"

    def test_changed_subnet_fails_the_operation_before_any_change(
        self,
        client: TestClient,
        owner: dict[str, str],
        running_cluster: dict[str, Any],
        run_worker: RunWorker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original = MockGcpProvider.describe_network

        def resized(self: MockGcpProvider, account: Any, lookup: Any) -> Any:
            details = original(self, account, lookup)
            details.subnets[0].cidr = "10.99.0.0/16"
            return details

        monkeypatch.setattr(MockGcpProvider, "describe_network", resized)
        scale = client.post(f"/api/v1/clusters/{running_cluster['id']}/scale", json={"node_count": 5}, headers=owner)
        run_worker()
        op = client.get(f"/api/v1/operations/{scale.json()['operation_id']}", headers=owner).json()
        assert op["status"] == "FAILED" and op["error_code"] == "NETWORK_CHANGED"
        assert {s["key"]: s["status"] for s in op["steps"]}["apply"] == "pending"

    def test_nodes_get_addresses_in_the_subnet(
        self, client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any]
    ) -> None:
        cidr = ipaddress.ip_network(running_cluster["network"]["subnets"][0]["cidr"])
        assert all(ipaddress.ip_address(n["private_ip"]) in cidr for n in running_cluster["nodes"])
        outputs = running_cluster["actual_state"]["infrastructure"]
        assert outputs["network"] == "projects/acme-prod/global/networks/prod-vpc"


class TestAws:
    def test_keyless_account_with_external_id(self, client: TestClient, owner: dict[str, str]) -> None:
        response = client.post(
            "/api/v1/cloud-accounts",
            json=aws_account_request(
                account="1234-5678-9012", role_arn="arn:aws:iam::123456789012:role/db-platform-provisioner"
            ),
            headers=owner,
        )
        assert response.status_code == 201, response.text
        account = response.json()
        assert (account["provider"], account["auth_type"], account["status"]) == ("aws", "assume_role", "CONNECTED")
        assert account["project_id"] == "123456789012" and account["service_account_email"] is None
        assert account["role_arn"] == "arn:aws:iam::123456789012:role/db-platform-provisioner"
        onboarding = client.get("/api/v1/cloud-accounts/onboarding?provider=aws", headers=owner).json()
        external_id = onboarding["external_id"]
        assert external_id.startswith("byoc-") and len(external_id) == 37
        condition = onboarding["trust_policy"]["Statement"][0]["Condition"]
        assert condition == {"StringEquals": {"sts:ExternalId": external_id}}
        assert onboarding["principal"].endswith(":role/byoc-org-" + onboarding["principal"].rsplit("-", 1)[-1])
        assert "ec2:RunInstances" in onboarding["permissions"]

    def test_external_ids_differ_per_organization(
        self, client: TestClient, owner: dict[str, str], login: Login
    ) -> None:
        acme = client.get("/api/v1/cloud-accounts/onboarding?provider=aws", headers=owner).json()
        globex = client.get(
            "/api/v1/cloud-accounts/onboarding?provider=aws", headers=login("owner@globex.example")
        ).json()
        assert acme["external_id"] != globex["external_id"] and acme["principal"] != globex["principal"]

    @pytest.mark.parametrize(
        ("overrides", "field"),
        [
            ({"project_id": "12345"}, "project_id"),
            ({"role_arn": "arn:aws:iam::999999999999:role/db-platform-provisioner"}, "role_arn"),
            ({"role_arn": "db-platform-provisioner"}, "role_arn"),
            ({"service_account_email": "x@acme-prod.iam.gserviceaccount.com"}, "service_account_email"),
            ({"region": "asia-south1"}, "region"),
        ],
    )
    def test_account_identifiers(
        self, client: TestClient, owner: dict[str, str], overrides: dict[str, Any], field: str
    ) -> None:
        response = client.post("/api/v1/cloud-accounts", json=aws_account_request(**overrides), headers=owner)
        assert response.status_code == 422 and field in response.json()["error"]["details"]["fields"]

    def test_gcp_accounts_do_not_take_a_role(self, client: TestClient, owner: dict[str, str]) -> None:
        body = account_request(role_arn="arn:aws:iam::123456789012:role/x")
        response = client.post("/api/v1/cloud-accounts", json=body, headers=owner)
        assert response.status_code == 422 and "role_arn" in response.json()["error"]["details"]["fields"]

    @pytest.mark.parametrize(
        ("role", "code"),
        [("untrusted-role", "AWS_ASSUME_ROLE_FAILED"), ("denied-role", "AWS_PERMISSION_DENIED")],
    )
    def test_validation_failures(self, client: TestClient, owner: dict[str, str], role: str, code: str) -> None:
        body = aws_account_request(role_arn=f"arn:aws:iam::123456789012:role/{role}")
        account = client.post("/api/v1/cloud-accounts", json=body, headers=owner).json()
        assert account["status"] == "FAILED" and account["validation"]["error"]["code"] == code

    def test_azure_is_not_offered(self, client: TestClient, owner: dict[str, str]) -> None:
        response = client.post("/api/v1/cloud-accounts", json={**account_request(), "provider": "azure"}, headers=owner)
        assert response.status_code == 422 and "cloud_provider" in response.json()["error"]["details"]["fields"]
        providers = client.get("/api/v1/cloud-providers", headers=owner).json()
        assert [(p["name"], p["auth_type"]) for p in providers] == [("gcp", "impersonation"), ("aws", "assume_role")]
        assert providers[1]["default_machine_type"] == "m6i.xlarge"

    def test_network_with_three_zones(self, client: TestClient, owner: dict[str, str]) -> None:
        network = aws_setup(client, owner)
        assert network["status"] == "AVAILABLE" and network["provider"] == "aws"
        assert network["zones"] == ["ap-south-1a", "ap-south-1b", "ap-south-1c"]
        details = network["details"]
        assert details["vpc"] == "vpc-0a1b2c3d4e5f67890" and details["vpc_cidrs"]
        vpc = ipaddress.ip_network(details["vpc_cidrs"][0])
        cidrs = [ipaddress.ip_network(s["cidr"]) for s in details["subnets"]]
        assert len(set(cidrs)) == 3 and all(c.subnet_of(vpc) for c in cidrs)

    @pytest.mark.parametrize(
        ("subnets", "code"),
        [
            (["subnet-0000dead"], "SUBNET_NOT_FOUND"),
            (["subnet-00000bad"], "SUBNET_NOT_IN_VPC"),
            (["subnet-0a1b2c30", "subnet-0a1b2c33"], "DUPLICATE_ZONE"),
        ],
    )
    def test_network_problems(self, client: TestClient, owner: dict[str, str], subnets: list[str], code: str) -> None:
        network = aws_setup(client, owner, subnets)
        assert network["status"] == "FAILED" and network["details"]["error"]["code"] == code

    def test_network_warnings(self, client: TestClient, owner: dict[str, str]) -> None:
        network = aws_setup(client, owner, ["subnet-0000beef", "subnet-0cafe001"])
        assert network["status"] == "AVAILABLE"
        warnings = " ".join(network["details"]["warnings"])
        assert "subnet-0000beef has no route to a NAT gateway" in warnings
        assert "subnet-0cafe001 assigns public IPv4 addresses" in warnings

    def test_ha_needs_three_availability_zones(self, client: TestClient, owner: dict[str, str]) -> None:
        network = aws_setup(client, owner, AWS_SUBNETS[:2])
        request = cluster_request(network, machine_type="m6i.2xlarge", storage_type="gp3")
        response = client.post("/api/v1/clusters", json=request, headers=owner)
        assert response.status_code == 422 and "high_availability" in response.json()["error"]["details"]["fields"]
        single = cluster_request(
            network, machine_type="m6i.2xlarge", storage_type="gp3", node_count=1, high_availability=False
        )
        assert client.post("/api/v1/clusters", json=single, headers=owner).status_code == 202

    def test_ha_cluster_lifecycle_on_aws(
        self, client: TestClient, owner: dict[str, str], run_worker: RunWorker
    ) -> None:
        network = aws_setup(client, owner)
        request = cluster_request(network, name="orders-search", machine_type="m6i.2xlarge", storage_type="gp3")
        created = client.post("/api/v1/clusters", json=request, headers=owner).json()
        run_worker()
        op = client.get(f"/api/v1/operations/{created['operation_id']}", headers=owner).json()
        assert op["status"] == "COMPLETED", op["error"]
        messages = " ".join(e["message"] for e in op["log"])
        assert "Launching EC2 instance" in messages and "security group" in messages
        assert "Creating VPC" not in messages

        cluster = client.get(f"/api/v1/clusters/{created['cluster_id']}", headers=owner).json()
        assert (cluster["cloud_provider"], cluster["lifecycle"], cluster["health"]) == ("aws", "ACTIVE", "HEALTHY")
        assert cluster["region"] == "ap-south-1" and cluster["project_id"] == "123456789012"
        assert cluster["environment"]["type"] == "TEST"
        subnets = {s["zone"]: ipaddress.ip_network(s["cidr"]) for s in cluster["network"]["subnets"]}
        for node in cluster["nodes"]:
            assert ipaddress.ip_address(node["private_ip"]) in subnets[node["zone"]]
            assert node["instance_id"].startswith("i-") and node["hostname"].endswith(".ap-south-1.compute.internal")
        assert sorted(n["zone"] for n in cluster["nodes"]) == ["ap-south-1a", "ap-south-1b", "ap-south-1c"]
        assert cluster["actual_state"]["infrastructure"]["security_group"].startswith("sg-")

        scale = client.post(f"/api/v1/clusters/{cluster['id']}/scale", json={"node_count": 4}, headers=owner).json()
        run_worker()
        assert client.get(f"/api/v1/operations/{scale['operation_id']}", headers=owner).json()["status"] == "COMPLETED"
        nodes = client.get(f"/api/v1/clusters/{cluster['id']}/nodes", headers=owner).json()
        assert nodes[3]["zone"] == "ap-south-1a", "the fourth node goes round-robin to the first zone"

        fault = client.post(
            f"/api/v1/mock/clusters/{cluster['id']}/faults",
            json={"node_name": "node-2", "fault": "vm_down"},
            headers=owner,
        )
        assert fault.status_code == 200
        deleted = delete_cluster(client, owner, cluster).json()
        run_worker()
        assert (
            client.get(f"/api/v1/operations/{deleted['operation_id']}", headers=owner).json()["status"] == "COMPLETED"
        )


class TestPermissionsAndIsolation:
    def test_operator_and_viewer_can_read_but_not_manage(
        self, client: TestClient, login: Login, network: dict[str, Any], cloud_account: dict[str, Any]
    ) -> None:
        for email in ("operator@acme.example", "viewer@acme.example"):
            headers = login(email)
            assert client.get("/api/v1/environments", headers=headers).status_code == 200
            path = f"/api/v1/environments/{network['environment_id']}/networks"
            assert client.get(path, headers=headers).json()[0]["id"] == network["id"]
            assert client.get(f"/api/v1/networks/{network['id']}", headers=headers).status_code == 200
            body = {
                "name": "n2",
                "cloud_account_id": cloud_account["id"],
                "region": "asia-south1",
                "vpc": "v",
                "subnets": ["s"],
            }
            for method, url, payload in (
                ("POST", "/api/v1/environments", {"name": "xy", "type": "TEST"}),
                ("DELETE", f"/api/v1/environments/{network['environment_id']}", None),
                ("POST", path, body),
                ("POST", "/api/v1/networks/lookup", {k: v for k, v in body.items() if k != "name"}),
                ("POST", f"/api/v1/networks/{network['id']}/validate", None),
                ("DELETE", f"/api/v1/networks/{network['id']}", None),
            ):
                response = client.request(method, url, json=payload, headers=headers)
                assert response.status_code == 403, (email, method, url, response.status_code)

    def test_operator_creates_clusters_in_registered_networks(
        self, client: TestClient, login: Login, network: dict[str, Any]
    ) -> None:
        request = cluster_request(network, name="ops-search", node_count=1, high_availability=False)
        assert client.post("/api/v1/clusters", json=request, headers=login("operator@acme.example")).status_code == 202

    def test_other_organizations_cannot_use_environments_or_networks(
        self, client: TestClient, login: Login, network: dict[str, Any], cloud_account: dict[str, Any]
    ) -> None:
        globex = login("owner@globex.example")
        assert client.get("/api/v1/environments", headers=globex).json() == []
        for method, url in (
            ("GET", f"/api/v1/environments/{network['environment_id']}"),
            ("GET", f"/api/v1/environments/{network['environment_id']}/networks"),
            ("DELETE", f"/api/v1/environments/{network['environment_id']}"),
            ("GET", f"/api/v1/networks/{network['id']}"),
            ("POST", f"/api/v1/networks/{network['id']}/validate"),
            ("DELETE", f"/api/v1/networks/{network['id']}"),
        ):
            assert client.request(method, url, headers=globex).status_code == 404, (method, url)
        # Globex cannot place a network in its own environment on Acme's cloud account.
        own = create_environment(client, globex, "production", "PRODUCTION")
        body = {
            "name": "net-a",
            "cloud_account_id": cloud_account["id"],
            "region": "asia-south1",
            "vpc": "v",
            "subnets": ["s"],
        }
        response = client.post(f"/api/v1/environments/{own['id']}/networks", json=body, headers=globex)
        assert response.status_code == 422 and "cloud_account_id" in response.json()["error"]["details"]["fields"]
        # Nor a cluster in Acme's network, even through its own environment.
        request = cluster_request(network, environment_id=own["id"])
        response = client.post("/api/v1/clusters", json=request, headers=globex)
        assert response.status_code == 422 and "network_id" in response.json()["error"]["details"]["fields"]


def test_clusters_created_before_networks_keep_their_dedicated_vpc(
    client: TestClient,
    owner: dict[str, str],
    running_cluster: dict[str, Any],
    platform: Any,
    run_worker: RunWorker,
) -> None:
    """What migration 0003 leaves behind: no network, no zones; scaling and deletion still work."""
    import uuid

    from app.infrastructure.db import session_scope
    from app.models import Cluster

    with session_scope(platform.session_factory) as s:
        cluster = s.get(Cluster, uuid.UUID(running_cluster["id"]))
        assert cluster is not None
        desired = dict(cluster.desired_state)
        desired.pop("network")
        desired["cloud"] = {k: v for k, v in desired["cloud"].items() if k != "zones"}
        cluster.desired_state = desired
        cluster.network_id = None
    scale = client.post(f"/api/v1/clusters/{running_cluster['id']}/scale", json={"node_count": 4}, headers=owner)
    assert scale.status_code == 202, scale.text
    run_worker()
    assert (
        client.get(f"/api/v1/operations/{scale.json()['operation_id']}", headers=owner).json()["status"] == "COMPLETED"
    )
    deleted = delete_cluster(client, owner, running_cluster).json()
    run_worker()
    op = client.get(f"/api/v1/operations/{deleted['operation_id']}", headers=owner).json()
    assert op["status"] == "COMPLETED"
    assert any("Destroying subnet and VPC network" in e["message"] for e in op["log"])
