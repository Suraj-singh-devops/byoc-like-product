"""Agent protocol: identity-verified registration, heartbeats, approved commands."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.application.agent_service import queue_command
from app.application.platform import Platform
from app.infrastructure.db import session_scope
from app.providers.database.base import AgentCommandSpec
from app.repositories import queries


def identity(client: TestClient, owner: dict[str, str], cluster_id: str, node: str) -> str:
    response = client.post(f"/api/v1/mock/clusters/{cluster_id}/nodes/{node}/identity-token", headers=owner)
    assert response.status_code == 200, response.text
    return response.json()["identity_token"]


def register(client: TestClient, cluster_id: str, node: str, token: str) -> Any:
    return client.post(
        "/api/v1/agent/register",
        json={"cluster_id": cluster_id, "node_name": node, "identity_token": token, "agent_version": "0.1.0"},
    )


def report(node: str) -> dict[str, Any]:
    return {
        "node_name": node,
        "agent_version": "0.1.0",
        "system": {"cpu_percent": 12.5, "memory_percent": 40.0, "disk_percent": 30.0},
        "engine": {"type": "elasticsearch", "reachable": True, "version": "9.5.4", "cluster_status": "green"},
    }


def test_register_heartbeat_and_commands(
    client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any], platform: Platform
) -> None:
    cluster_id = running_cluster["id"]
    response = register(client, cluster_id, "node-1", identity(client, owner, cluster_id, "node-1"))
    assert response.status_code == 200, response.text
    agent_token = response.json()["agent_token"]
    headers = {"Authorization": f"Bearer {agent_token}"}

    beat = client.post("/api/v1/agent/heartbeat", json={"report": report("node-1")}, headers=headers)
    assert beat.status_code == 200 and beat.json()["commands"] == []
    node = next(
        n for n in client.get(f"/api/v1/clusters/{cluster_id}/nodes", headers=owner).json() if n["name"] == "node-1"
    )
    assert (
        node["report_source"] == "heartbeat"
        and node["agent_registered"] is True
        and node["agent_status"] == "REPORTING"
    )
    assert node["system"]["cpu_percent"] == 12.5

    with session_scope(platform.session_factory) as s:
        target = next(n for n in queries.active_nodes(s, running_cluster_uuid(running_cluster)) if n.name == "node-1")
        queue_command(s, target, AgentCommandSpec("restart_engine"), None)
    commands = client.post("/api/v1/agent/heartbeat", json={"report": report("node-1")}, headers=headers).json()[
        "commands"
    ]
    assert [c["command"] for c in commands] == ["restart_engine"]
    result = client.post(
        f"/api/v1/agent/commands/{commands[0]['id']}/result",
        json={"status": "succeeded", "message": "restarted"},
        headers=headers,
    )
    assert result.status_code == 204
    again = client.post("/api/v1/agent/heartbeat", json={"report": report("node-1")}, headers=headers).json()
    assert again["commands"] == []


def running_cluster_uuid(cluster: dict[str, Any]) -> Any:
    import uuid

    return uuid.UUID(cluster["id"])


def test_identity_of_another_node_is_rejected(
    client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any]
) -> None:
    cluster_id = running_cluster["id"]
    token = identity(client, owner, cluster_id, "node-2")
    response = register(client, cluster_id, "node-1", token)
    assert response.status_code == 401


def test_forged_identity_and_unknown_tokens(client: TestClient, running_cluster: dict[str, Any]) -> None:
    assert register(client, running_cluster["id"], "node-1", "eyJhbGciOiJIUzI1NiJ9.e30.forged").status_code == 401
    unknown = client.post(
        "/api/v1/agent/heartbeat", json={"report": report("node-1")}, headers={"Authorization": "Bearer byoca_nope"}
    )
    assert unknown.status_code == 401
    user_session = client.post("/api/v1/agent/heartbeat", json={"report": report("node-1")})
    assert user_session.status_code == 401


def test_report_for_another_node_is_ignored(
    client: TestClient, owner: dict[str, str], running_cluster: dict[str, Any]
) -> None:
    cluster_id = running_cluster["id"]
    token = register(client, cluster_id, "node-1", identity(client, owner, cluster_id, "node-1")).json()["agent_token"]
    spoof = report("node-2")
    spoof["system"]["cpu_percent"] = 99.0
    client.post("/api/v1/agent/heartbeat", json={"report": spoof}, headers={"Authorization": f"Bearer {token}"})
    node2 = next(
        n for n in client.get(f"/api/v1/clusters/{cluster_id}/nodes", headers=owner).json() if n["name"] == "node-2"
    )
    assert node2["system"].get("cpu_percent") != 99.0


def test_commands_are_allowlisted(platform: Platform, running_cluster: dict[str, Any]) -> None:
    import pytest

    from app.domain.errors import ValidationFailed

    with session_scope(platform.session_factory) as s:
        node = queries.active_nodes(s, running_cluster_uuid(running_cluster))[0]
        with pytest.raises(ValidationFailed):
            queue_command(s, node, AgentCommandSpec("run_shell", {"cmd": "rm -rf /"}), None)
        with pytest.raises(ValidationFailed):
            queue_command(s, node, AgentCommandSpec("restart_engine", {"flags": "--force"}), None)
        # Scale-down commands are not approved in the MVP (docs/adr/0007).
        for removed in ("drain_nodes", "undrain_nodes"):
            with pytest.raises(ValidationFailed):
                queue_command(s, node, AgentCommandSpec(removed, {}), None)
