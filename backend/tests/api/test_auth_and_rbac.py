from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import DEMO_PASSWORD, account_request, cluster_request, delete_cluster

Login = Callable[..., dict[str, str]]


class TestAuthentication:
    def test_login_sets_http_only_cookie(self, client: TestClient) -> None:
        response = client.post("/api/v1/auth/login", json={"email": "Owner@Acme.example ", "password": DEMO_PASSWORD})
        assert response.status_code == 200
        body = response.json()
        assert body["role"] == "OWNER" and body["organization"]["name"] == "Acme Corp"
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie

    def test_cookie_session_works(self, client: TestClient) -> None:
        client.post("/api/v1/auth/login", json={"email": "viewer@acme.example", "password": DEMO_PASSWORD})
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "VIEWER"
        assert "cluster:create" not in me.json()["permissions"]
        client.post("/api/v1/auth/logout")
        assert client.get("/api/v1/auth/me").status_code == 401

    def test_wrong_password_and_unknown_user_look_the_same(self, client: TestClient) -> None:
        wrong = client.post("/api/v1/auth/login", json={"email": "owner@acme.example", "password": "nope-nope"})
        unknown = client.post("/api/v1/auth/login", json={"email": "ghost@acme.example", "password": "nope-nope"})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]

    def test_failed_login_is_audited(self, client: TestClient, owner: dict[str, str]) -> None:
        client.post("/api/v1/auth/login", json={"email": "admin@acme.example", "password": "wrong-password"})
        logs = client.get("/api/v1/audit-logs?action=LOGIN_FAILED&status=FAILURE", headers=owner).json()
        assert logs["total"] == 1 and logs["items"][0]["user"] == "admin@acme.example"

    def test_rate_limited(self, client: TestClient) -> None:
        for _ in range(10):
            client.post("/api/v1/auth/login", json={"email": "owner@acme.example", "password": "wrong-password"})
        response = client.post("/api/v1/auth/login", json={"email": "owner@acme.example", "password": DEMO_PASSWORD})
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "RATE_LIMITED"

    def test_every_endpoint_requires_auth(self, client: TestClient) -> None:
        for path in (
            "/api/v1/clusters",
            "/api/v1/cloud-accounts",
            "/api/v1/operations",
            "/api/v1/audit-logs",
            "/api/v1/engines",
        ):
            response = client.get(path)
            assert response.status_code == 401, path
            assert response.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    def test_garbage_token(self, client: TestClient) -> None:
        response = client.get("/api/v1/clusters", headers={"Authorization": "Bearer not-a-jwt"})
        assert response.status_code == 401

    def test_signup_creates_isolated_org(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/signup",
            json={
                "name": "Nia",
                "email": "nia@initech.example",
                "password": "initech-pass",
                "organization_name": "Initech",
            },
        )
        assert response.status_code == 201
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        assert response.json()["role"] == "OWNER"
        assert client.get("/api/v1/clusters", headers=headers).json() == []
        duplicate = client.post(
            "/api/v1/auth/signup",
            json={
                "name": "X",
                "email": "nia@initech.example",
                "password": "initech-pass",
                "organization_name": "Other",
            },
        )
        assert duplicate.status_code == 409

    def test_meta_lists_demo_accounts_in_mock_mode(self, client: TestClient) -> None:
        meta = client.get("/api/v1/meta").json()
        assert meta["mock_mode"] is True
        assert {a["email"] for a in meta["demo_accounts"]} >= {"owner@acme.example", "operator@acme.example"}
        assert {a["role"] for a in meta["demo_accounts"]} == {"OWNER", "ADMIN", "OPERATOR", "VIEWER"}

    def test_errors_have_request_id_and_no_internals(self, client: TestClient) -> None:
        response = client.get("/api/v1/nope", headers={"X-Request-ID": "req-123"})
        assert response.status_code == 404
        assert response.headers["x-request-id"] == "req-123"
        assert response.json()["error"]["request_id"] == "req-123"
        assert response.headers["x-content-type-options"] == "nosniff"


class TestRbac:
    def test_viewer_is_read_only(self, client: TestClient, login: Login, network: dict[str, Any]) -> None:
        viewer = login("viewer@acme.example")
        assert client.get("/api/v1/clusters", headers=viewer).status_code == 200
        response = client.post("/api/v1/clusters", json=cluster_request(network), headers=viewer)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        account = client.post("/api/v1/cloud-accounts", json=account_request("acme-dev"), headers=viewer)
        assert account.status_code == 403

    def test_operator_permissions(self, client: TestClient, login: Login) -> None:
        operator = login("operator@acme.example")
        me = client.get("/api/v1/auth/me", headers=operator).json()
        assert me["role"] == "OPERATOR"
        assert set(me["permissions"]) == {
            "audit:read",
            "cloud_account:read",
            "cluster:create",
            "cluster:health_check",
            "cluster:operate",
            "cluster:read",
            "cluster:scale",
            "environment:read",
            "member:read",
            "network:read",
            "operation:manage",
            "operation:read",
        }

    def test_operator_manages_any_cluster_but_cannot_delete(
        self,
        client: TestClient,
        login: Login,
        running_cluster: dict[str, Any],
        cloud_account: dict[str, Any],
        network: dict[str, Any],
    ) -> None:
        operator = login("operator@acme.example")
        cluster_id = running_cluster["id"]
        # Clusters created by someone else can be scaled and health-checked.
        assert client.post(f"/api/v1/clusters/{cluster_id}/health-check", headers=operator).status_code == 202
        scale = client.post(f"/api/v1/clusters/{cluster_id}/scale", json={"node_count": 5}, headers=operator)
        assert scale.status_code == 202
        assert client.post(
            f"/api/v1/operations/{scale.json()['operation_id']}/cancel", headers=operator
        ).status_code == (200)
        response = delete_cluster(client, operator, running_cluster)
        assert response.status_code == 403
        assert response.json()["error"]["details"]["required_permission"] == "cluster:delete"
        own = client.post(
            "/api/v1/clusters",
            json=cluster_request(network, name="ops-search", node_count=1, high_availability=False),
            headers=operator,
        )
        assert own.status_code == 202
        assert (
            delete_cluster(client, operator, {"id": own.json()["cluster_id"], "name": "ops-search"}).status_code == 403
        )

    def test_operator_cannot_touch_deletes_accounts_or_members(
        self,
        client: TestClient,
        login: Login,
        owner: dict[str, str],
        running_cluster: dict[str, Any],
        cloud_account: dict[str, Any],
    ) -> None:
        operator = login("operator@acme.example")
        deletion = delete_cluster(client, owner, running_cluster).json()
        cancel = client.post(f"/api/v1/operations/{deletion['operation_id']}/cancel", headers=operator)
        assert cancel.status_code == 403
        for method, path, body in (
            ("POST", "/api/v1/cloud-accounts", account_request("acme-dev")),
            ("POST", f"/api/v1/cloud-accounts/{cloud_account['id']}/validate", None),
            ("DELETE", f"/api/v1/cloud-accounts/{cloud_account['id']}", None),
            (
                "POST",
                "/api/v1/organizations/current/members",
                {"email": "x@acme.example", "role": "VIEWER", "password": "x-password"},
            ),
        ):
            response = client.request(method, path, json=body, headers=operator)
            assert response.status_code == 403, (method, path, response.status_code)

    def test_developer_role_no_longer_exists(self, client: TestClient, owner: dict[str, str]) -> None:
        response = client.post(
            "/api/v1/organizations/current/members",
            json={"email": "dev@acme.example", "role": "DEVELOPER", "password": "dev-password"},
            headers=owner,
        )
        assert response.status_code == 422

    def test_admin_cannot_create_owners(self, client: TestClient, login: Login) -> None:
        admin = login("admin@acme.example")
        response = client.post(
            "/api/v1/organizations/current/members",
            json={"email": "boss@acme.example", "role": "OWNER", "password": "boss-password"},
            headers=admin,
        )
        assert response.status_code == 403

    def test_member_management_and_audit(self, client: TestClient, owner: dict[str, str], login: Login) -> None:
        added = client.post(
            "/api/v1/organizations/current/members",
            json={"email": "new@acme.example", "name": "New", "role": "VIEWER", "password": "new-password"},
            headers=owner,
        )
        assert added.status_code == 201
        user_id = added.json()["user_id"]
        assert login("new@acme.example", "new-password")
        updated = client.patch(
            f"/api/v1/organizations/current/members/{user_id}", json={"role": "OPERATOR"}, headers=owner
        )
        assert updated.json()["role"] == "OPERATOR"
        assert client.delete(f"/api/v1/organizations/current/members/{user_id}", headers=owner).status_code == 204
        actions = [e["action"] for e in client.get("/api/v1/audit-logs", headers=owner).json()["items"]]
        assert {"MEMBER_ADDED", "MEMBER_ROLE_CHANGED", "MEMBER_REMOVED"} <= set(actions)

    def test_removed_member_loses_access_immediately(
        self, client: TestClient, owner: dict[str, str], login: Login
    ) -> None:
        viewer = login("viewer@acme.example")
        members = client.get("/api/v1/organizations/current/members", headers=owner).json()
        viewer_id = next(m["user_id"] for m in members if m["email"] == "viewer@acme.example")
        client.delete(f"/api/v1/organizations/current/members/{viewer_id}", headers=owner)
        assert client.get("/api/v1/clusters", headers=viewer).status_code == 401

    def test_last_owner_is_protected(self, client: TestClient, owner: dict[str, str]) -> None:
        members = client.get("/api/v1/organizations/current/members", headers=owner).json()
        owner_id = next(m["user_id"] for m in members if m["role"] == "OWNER")
        response = client.patch(
            f"/api/v1/organizations/current/members/{owner_id}", json={"role": "VIEWER"}, headers=owner
        )
        assert response.status_code == 409


class TestTenantIsolation:
    def test_other_org_cannot_see_or_touch_resources(
        self,
        client: TestClient,
        login: Login,
        running_cluster: dict[str, Any],
        cloud_account: dict[str, Any],
        network: dict[str, Any],
    ) -> None:
        globex = login("owner@globex.example")
        cluster_id = running_cluster["id"]
        assert client.get("/api/v1/clusters", headers=globex).json() == []
        assert client.get("/api/v1/cloud-accounts", headers=globex).json() == []
        assert client.get("/api/v1/operations", headers=globex).json()["total"] == 0
        assert client.get("/api/v1/events", headers=globex).json() == []
        for method, path in (
            ("GET", f"/api/v1/clusters/{cluster_id}"),
            ("GET", f"/api/v1/clusters/{cluster_id}/nodes"),
            ("GET", f"/api/v1/clusters/{cluster_id}/metrics"),
            ("GET", f"/api/v1/clusters/{cluster_id}/health"),
            ("DELETE", f"/api/v1/clusters/{cluster_id}"),
            ("GET", f"/api/v1/cloud-accounts/{cloud_account['id']}"),
            ("DELETE", f"/api/v1/cloud-accounts/{cloud_account['id']}"),
        ):
            response = client.request(method, path, headers=globex)
            assert response.status_code == 404, (method, path, response.status_code)
        scale = client.post(f"/api/v1/clusters/{cluster_id}/scale", json={"node_count": 5}, headers=globex)
        assert scale.status_code == 404
        assert delete_cluster(client, globex, running_cluster).status_code == 404
        # Using another org's cloud account to create a cluster is also impossible.
        create = client.post("/api/v1/clusters", json=cluster_request(network), headers=globex)
        assert create.status_code == 422
        # The organization always comes from the session, never from the request body.
        spoofed = client.post(
            "/api/v1/clusters",
            json={**cluster_request(network), "organization_id": running_cluster["id"]},
            headers=globex,
        )
        assert spoofed.status_code == 422 and "organization_id" in spoofed.json()["error"]["details"]["fields"]

    def test_audit_logs_are_per_org(self, client: TestClient, login: Login, owner: dict[str, str]) -> None:
        globex = login("owner@globex.example")
        acme_users = {e["user"] for e in client.get("/api/v1/audit-logs", headers=owner).json()["items"]}
        globex_users = {e["user"] for e in client.get("/api/v1/audit-logs", headers=globex).json()["items"]}
        assert "owner@globex.example" not in acme_users
        assert globex_users == {"owner@globex.example"}
