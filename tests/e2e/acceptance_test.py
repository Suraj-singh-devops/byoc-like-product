#!/usr/bin/env python3
"""Walk the PRD acceptance criteria and the v2 operating rules against a running stack.

    docker compose up --build -d
    python3 tests/e2e/acceptance_test.py [--base-url http://localhost:3000] [--keep]

Requests go through the web UI's origin (the Next.js server proxies /api to the control
plane), so this covers UI server -> API -> PostgreSQL -> Redis -> worker -> provider.
Standard library only.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from typing import Any

PASSWORD = "demo-password"
results: list[tuple[str, bool, str]] = []


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with self.opener.open(req, timeout=30) as resp:
                return resp.status, self._decode(resp.read(), resp.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as err:
            return err.code, self._decode(err.read(), err.headers.get("Content-Type", ""))

    @staticmethod
    def _decode(raw: bytes, content_type: str) -> Any:
        if not raw:
            return None
        if "json" in content_type:
            return json.loads(raw)
        return raw.decode(errors="replace")

    def api(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
        return self.request(method, f"/api/v1{path}", body, headers)

    def login(self, email: str) -> int:
        status, _ = self.api("POST", "/auth/login", {"email": email, "password": PASSWORD})
        return status


def check(number: str, description: str, ok: bool, detail: str = "") -> bool:
    results.append((f"{number}. {description}", ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {number}. {description}{f' - {detail}' if detail else ''}", flush=True)
    return ok


def wait_for_operation(client: Client, operation_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = None
    while True:
        status, op = client.api("GET", f"/operations/{operation_id}")
        if status != 200:
            raise RuntimeError(f"GET operation returned {status}: {op}")
        marker = (op["status"], op["current_step"])
        if marker != last:
            print(f"      {op['operation_type']}: {op['status']:<13} step={op['current_step'] or '-':<15} {op['progress']:>3}%", flush=True)
            last = marker
        if op["is_terminal"]:
            return op
        if time.monotonic() > deadline:
            raise TimeoutError(f"operation {operation_id} did not finish within {timeout}s")
        time.sleep(1.5)


def settled(client: Client, path: str, busy: tuple[str, ...] = ("PENDING", "VALIDATING"), timeout: float = 60) -> Any:
    """Validation runs asynchronously in the workers: poll until the status settles."""
    deadline = time.monotonic() + timeout
    while True:
        _, body = client.api("GET", path)
        if body.get("status") not in busy or time.monotonic() > deadline:
            return body
        time.sleep(0.5)


def wait_until(predicate: Any, timeout: float, interval: float = 2.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            return value
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:3000")
    parser.add_argument(
        "--keep", action="store_true", help="keep the cloud accounts, environments and networks created by the run"
    )
    args = parser.parse_args()
    suffix = secrets.token_hex(2)
    owner = Client(args.base_url)

    print(f"Acceptance run against {args.base_url} (suffix {suffix})\n")
    print("Setup")
    status, body = owner.request("GET", "/login")
    check("3", "Web UI is served", status == 200, f"HTTP {status}")
    status, meta = owner.api("GET", "/meta")
    if status != 200:
        check("2", "Stack is running (docker compose up)", False, f"/api/v1/meta returned {status}")
        return 1
    check("2", "Stack is running (docker compose up)", True, f"mock_mode={meta['mock_mode']}")

    check("4", "Log in", owner.login("owner@acme.example") == 200, "owner@acme.example")
    status, me = owner.api("GET", "/auth/me")
    check("4b", "Session cookie authenticates API calls", status == 200 and me["role"] == "OWNER")

    print("\nCloud account (keyless)")
    project = f"acme-e2e-{suffix}"
    status, rejected = owner.api(
        "POST",
        "/cloud-accounts",
        {"name": f"key-{suffix}", "project_id": project, "region": "asia-south1", "service_account_key": "{}"},
    )
    check("5a", "Service-account keys are rejected", status == 422, f"HTTP {status}")
    status, account = owner.api(
        "POST",
        "/cloud-accounts",
        {
            "name": f"e2e-{suffix}",
            "project_id": project,
            "region": "asia-south1",
            "service_account_email": f"db-platform-provisioner@{project}.iam.gserviceaccount.com",
            "validate_now": False,
        },
    )
    check("5", "Add a GCP project/account", status == 201 and account["status"] == "PENDING", f"project {project}")
    status, account = owner.api("POST", f"/cloud-accounts/{account['id']}/validate")
    check("6a", "Validation is handed to the terraform-runner", status == 200 and account["status"] == "VALIDATING")
    account = settled(owner, f"/cloud-accounts/{account['id']}")
    check("6", "Validate GCP access", account["status"] == "CONNECTED", account.get("status", ""))

    print("\nEnvironment and network (existing VPC and subnet)")
    operator = Client(args.base_url)
    operator.login("operator@acme.example")
    status, _ = operator.api("POST", "/environments", {"name": f"op-{suffix}", "type": "TEST"})
    check("ENV-a", "Operator cannot create environments", status == 403, f"HTTP {status}")
    status, environment = owner.api("POST", "/environments", {"name": f"e2e-prod-{suffix}", "type": "PRODUCTION"})
    check("ENV", "Create a production environment", status == 201 and environment["type"] == "PRODUCTION", environment.get("name", ""))
    lookup = {"cloud_account_id": account["id"], "region": "asia-south1", "vpc": "prod-vpc", "subnets": ["db-subnet"]}
    status, details = owner.api("POST", "/networks/lookup", lookup)
    check(
        "NET-a",
        "Fetch network details from the cloud before registering",
        status == 200 and details["valid"] and len(details["zones"]) == 3,
        f"{details.get('subnets', [{}])[0].get('cidr')} in {details.get('zones')}",
    )
    status, network = owner.api("POST", f"/environments/{environment['id']}/networks", {**lookup, "name": "prod-network"})
    network = settled(owner, f"/networks/{network['id']}") if status == 201 else network
    check(
        "NET",
        "Register the network (platform record; nothing created in the cloud)",
        status == 201 and network["status"] == "AVAILABLE" and network["details"]["vpc"].endswith("/networks/prod-vpc"),
        network.get("status", ""),
    )

    print("\nCreate cluster")
    request = {
        "name": f"e2e-search-{suffix}",
        "engine": "elasticsearch",
        "version": "9.5.4",
        "environment_id": environment["id"],
        "network_id": network["id"],
        "zone": "asia-south1-a",
        "machine_type": "e2-standard-8",
        "node_count": 3,
        "storage_gb": 500,
        "storage_type": "pd-balanced",
        "high_availability": True,
    }
    status, body = owner.api("POST", "/clusters", {**request, "version": "latest"})
    check("7a", "Only exact catalog versions are accepted ('latest' rejected)", status == 422, f"HTTP {status}")
    status, accepted = owner.api("POST", "/clusters", request, {"Idempotency-Key": f"e2e-{suffix}"})
    ok = status == 202 and set(accepted) == {"cluster_id", "operation_id", "lifecycle"} and accepted["lifecycle"] == "CREATING"
    check("7-12", "Create an Elasticsearch cluster in the network (VM type, disk, nodes)", ok, json.dumps(accepted))
    if not ok:
        return 1
    cluster_id = accepted["cluster_id"]
    status, again = owner.api("POST", "/clusters", request, {"Idempotency-Key": f"e2e-{suffix}"})
    check("7b", "Retried create with the same Idempotency-Key is deduplicated", again == accepted)

    op = wait_for_operation(owner, accepted["operation_id"], timeout=240)
    steps = [s["key"] for s in op["steps"] if s["status"] == "completed"]
    check("13", "See provisioning progress", op["status"] == "COMPLETED", f"{op['status']}; steps {steps}")
    status, cluster = owner.api("GET", f"/clusters/{cluster_id}")
    check(
        "14",
        "See cluster lifecycle and health (stored separately)",
        cluster["lifecycle"] == "ACTIVE" and cluster["health"] == "HEALTHY" and cluster["engine_version"] == "9.5.4",
        f"{cluster['lifecycle']}/{cluster['health']} {cluster['engine_version']}",
    )
    check(
        "14b",
        "Cluster records its environment and network",
        cluster["environment"]["id"] == environment["id"] and cluster["network"]["id"] == network["id"],
        f"{cluster['environment']['name']} / {cluster['network']['name']}",
    )
    nodes = cluster["nodes"]
    zones = sorted({n["zone"] for n in nodes})
    check(
        "15",
        "See node information",
        len(nodes) == 3 and all(n["private_ip"] and n["agent_status"] == "REPORTING" for n in nodes),
        f"{len(nodes)} nodes in {zones}",
    )

    def metrics_ready() -> dict[str, Any] | None:
        _, metrics = owner.api("GET", f"/clusters/{cluster_id}/metrics?minutes=15")
        current = metrics.get("current", {})
        return metrics if current.get("cpu_percent") is not None and metrics.get("history") else None

    metrics = wait_until(metrics_ready, timeout=60)
    current = (metrics or {}).get("current", {})
    check(
        "16",
        "See basic metrics",
        bool(metrics),
        f"CPU {current.get('cpu_percent')}%, memory {current.get('memory_percent')}%, disk {current.get('disk_percent')}%, "
        f"heap {current.get('engine', {}).get('jvm_heap_percent')}%",
    )

    print("\nAccess control")
    viewer = Client(args.base_url)
    viewer.login("viewer@acme.example")
    status, _ = viewer.api("POST", f"/clusters/{cluster_id}/scale", {"node_count": 5})
    check("RBAC", "Viewer cannot scale the cluster", status == 403, f"HTTP {status}")
    status, _ = operator.api("DELETE", f"/clusters/{cluster_id}?confirm={request['name']}")
    check("RBAC-2", "Operator cannot delete a cluster", status == 403, f"HTTP {status}")
    globex = Client(args.base_url)
    globex.login("owner@globex.example")
    status, _ = globex.api("GET", f"/clusters/{cluster_id}")
    check("Tenancy", "Another organization cannot see the cluster", status == 404, f"HTTP {status}")
    status, _ = globex.api("GET", f"/networks/{network['id']}")
    check("Tenancy-2", "Another organization cannot see the network", status == 404, f"HTTP {status}")
    status, _ = owner.api("DELETE", f"/networks/{network['id']}")
    check("NET-b", "A network in use cannot be removed", status == 409, f"HTTP {status}")

    print("\nFailure detection (mock fault injection)")
    if meta["mock_mode"]:
        owner.api("POST", f"/mock/clusters/{cluster_id}/faults", {"node_name": "node-2", "fault": "vm_down"})

        def degraded() -> dict[str, Any] | None:
            _, c = owner.api("GET", f"/clusters/{cluster_id}")
            return c if c["health"] == "DEGRADED" else None

        degraded_cluster = wait_until(degraded, timeout=60)
        _, events = owner.api("GET", f"/clusters/{cluster_id}/events")
        detected = any(e["event_type"] == "NODE_UNHEALTHY" and e["node_name"] == "node-2" for e in events)
        lifecycle = (degraded_cluster or {}).get("lifecycle")
        check(
            "26",
            "Node failure detected (VM down -> cluster DEGRADED + event; lifecycle stays ACTIVE)",
            bool(degraded_cluster) and detected and lifecycle == "ACTIVE",
        )
        owner.api("POST", f"/mock/clusters/{cluster_id}/faults", {"node_name": "node-2", "fault": "clear"})

        def recovered() -> dict[str, Any] | None:
            _, c = owner.api("GET", f"/clusters/{cluster_id}")
            return c if c["health"] == "HEALTHY" else None

        check("26b", "Recovery detected", bool(wait_until(recovered, timeout=60)))

    print("\nScale (up only)")
    status, body = owner.api("POST", f"/clusters/{cluster_id}/scale", {"node_count": 2})
    code = (body or {}).get("error", {}).get("code") if isinstance(body, dict) else None
    check("17a", "Scale-down is rejected explicitly", status == 422 and code == "SCALE_DOWN_NOT_SUPPORTED", f"{status} {code}")
    status, scale = operator.api("POST", f"/clusters/{cluster_id}/scale", {"node_count": 5})
    check("17", "Operator scales the cluster (3 -> 5)", status == 202, json.dumps(scale))
    op = wait_for_operation(owner, scale["operation_id"], timeout=240)
    _, cluster = owner.api("GET", f"/clusters/{cluster_id}")
    check(
        "18",
        "See the scaling operation",
        op["status"] == "COMPLETED" and len(cluster["nodes"]) == 5,
        f"{op['status']}; {len(cluster['nodes'])} nodes; generation {cluster['generation']}/{cluster['observed_generation']}",
    )
    status, health_op = owner.api("POST", f"/clusters/{cluster_id}/health-check")
    health_op = wait_for_operation(owner, health_op["id"], timeout=60)
    check(
        "18b",
        "Health check after scaling",
        health_op["status"] == "COMPLETED" and (health_op.get("result") or {}).get("state") == "HEALTHY",
        (health_op.get("result") or {}).get("state", ""),
    )

    print("\nDelete")
    status, body = owner.api("DELETE", f"/clusters/{cluster_id}")
    code = (body or {}).get("error", {}).get("code") if isinstance(body, dict) else None
    check("19a", "Delete without typed confirmation is refused", status == 422 and code == "CONFIRMATION_REQUIRED", f"{status} {code}")
    status, deletion = owner.api("DELETE", f"/clusters/{cluster_id}?confirm={request['name']}")
    check("19b", "Confirmed delete accepted", status == 202 and deletion["lifecycle"] == "DELETING", json.dumps(deletion))
    op = wait_for_operation(owner, deletion["operation_id"], timeout=180)
    _, cluster = owner.api("GET", f"/clusters/{cluster_id}")
    check("19", "Delete the cluster", op["status"] == "COMPLETED" and cluster["lifecycle"] == "DELETED", cluster["lifecycle"])

    print("\nDelete pre-empts a running operation")
    quick = {**request, "name": f"e2e-quick-{suffix}", "node_count": 1, "high_availability": False, "storage_gb": 50}
    status, created = owner.api("POST", "/clusters", quick)

    def running() -> dict[str, Any] | None:
        _, o = owner.api("GET", f"/operations/{created['operation_id']}")
        return o if o["status"] not in ("PENDING",) else None

    wait_until(running, timeout=30, interval=0.5)
    status, deletion = owner.api("DELETE", f"/clusters/{created['cluster_id']}?confirm={quick['name']}")
    delete_op = wait_for_operation(owner, deletion["operation_id"], timeout=240)
    _, create_op = owner.api("GET", f"/operations/{created['operation_id']}")
    check(
        "FR-9",
        "Deletion stops the running create and then removes the cluster",
        create_op["status"] == "CANCELLED" and delete_op["status"] == "COMPLETED",
        f"create {create_op['status']}, delete {delete_op['status']}",
    )

    print("\nAudit")
    _, audit = owner.api("GET", f"/audit-logs?resource_id={cluster_id}&limit=50")
    seen = {(e["action"], e["status"]) for e in audit["items"]}
    expected = {
        ("CLUSTER_CREATE_STARTED", "SUCCESS"),
        ("CLUSTER_CREATED", "SUCCESS"),
        ("CLUSTER_SCALE_STARTED", "SUCCESS"),
        ("CLUSTER_SCALE_COMPLETED", "SUCCESS"),
        ("CLUSTER_DELETE_STARTED", "SUCCESS"),
        ("CLUSTER_DELETE_COMPLETED", "SUCCESS"),
    }
    entry = next((e for e in audit["items"] if e["action"] == "CLUSTER_SCALE_STARTED"), {})
    check(
        "20",
        "View audit logs (TRD event names)",
        expected <= seen and entry.get("user") == "operator@acme.example",
        f"{len(audit['items'])} entries for the cluster",
    )

    print("\nAWS (simulated): keyless role, three-zone network, HA cluster")
    status, onboarding = owner.api("GET", "/cloud-accounts/onboarding?provider=aws")
    external_id = (onboarding or {}).get("external_id") or ""
    check("AWS-a", "The organization has an AWS external ID for its trust policy", external_id.startswith("byoc-"), external_id)
    aws_id = "123456789012"
    status, aws_account = owner.api(
        "POST",
        "/cloud-accounts",
        {
            "name": f"e2e-aws-{suffix}",
            "provider": "aws",
            "project_id": aws_id,
            "region": "ap-south-1",
            "role_arn": f"arn:aws:iam::{aws_id}:role/db-platform-provisioner",
        },
    )
    aws_account = settled(owner, f"/cloud-accounts/{aws_account['id']}") if status == 201 else aws_account
    check("AWS-b", "Add an AWS account (assumed role, no keys)", aws_account["status"] == "CONNECTED", aws_account.get("status", ""))
    status, test_env = owner.api("POST", "/environments", {"name": f"e2e-test-{suffix}", "type": "TEST"})
    subnets = ["subnet-0a1b2c3d4e5f60000", "subnet-0a1b2c3d4e5f60001", "subnet-0a1b2c3d4e5f60002"]
    status, aws_network = owner.api(
        "POST",
        f"/environments/{test_env['id']}/networks",
        {"name": "aws-private", "cloud_account_id": aws_account["id"], "region": "ap-south-1", "vpc": "vpc-0a1b2c3d4e5f67890", "subnets": subnets},
    )
    aws_network = settled(owner, f"/networks/{aws_network['id']}") if status == 201 else aws_network
    check(
        "AWS-c",
        "Register an AWS VPC with subnets in three availability zones",
        status == 201 and aws_network["status"] == "AVAILABLE" and len(aws_network["zones"]) == 3,
        ", ".join(aws_network.get("zones", [])),
    )
    aws_request = {
        "name": f"e2e-aws-{suffix}",
        "environment_id": test_env["id"],
        "network_id": aws_network["id"],
        "machine_type": "m6i.2xlarge",
        "node_count": 3,
        "storage_gb": 100,
        "storage_type": "gp3",
        "high_availability": True,
    }
    status, aws_created = owner.api("POST", "/clusters", aws_request)
    aws_op = wait_for_operation(owner, aws_created["operation_id"], timeout=240) if status == 202 else {"status": status}
    _, aws_cluster = owner.api("GET", f"/clusters/{aws_created.get('cluster_id')}")
    aws_zones = sorted(n["zone"] for n in aws_cluster.get("nodes", []))
    check(
        "AWS-d",
        "HA cluster on AWS: one node per availability zone, in the registered subnets",
        aws_op["status"] == "COMPLETED" and aws_cluster.get("health") == "HEALTHY" and len(set(aws_zones)) == 3,
        f"{aws_op['status']}; {aws_zones}",
    )
    status, deletion = owner.api("DELETE", f"/clusters/{aws_cluster['id']}?confirm={aws_request['name']}")
    aws_delete = wait_for_operation(owner, deletion["operation_id"], timeout=180)
    check("AWS-e", "Delete the AWS cluster", aws_delete["status"] == "COMPLETED", aws_delete["status"])

    if not args.keep:
        for net in (network, aws_network):
            owner.api("DELETE", f"/networks/{net['id']}")
        for env in (environment, test_env):
            owner.api("DELETE", f"/environments/{env['id']}")
        for acct in (account, aws_account):
            owner.api("DELETE", f"/cloud-accounts/{acct['id']}")

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("Failed:\n  " + "\n  ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
