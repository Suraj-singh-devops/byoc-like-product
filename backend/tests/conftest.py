from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.platform import Platform
from app.application.provisioning.runner import OperationRunner
from app.application.task_handlers import inline_tasks
from app.config.settings import Settings
from app.infrastructure.db import SessionFactory, create_db_engine, create_session_factory
from app.infrastructure.logging import configure_logging
from app.infrastructure.queue import InMemoryOperationQueue
from app.infrastructure.rate_limit import InMemoryRateLimiter
from app.main import create_app
from app.models import Base
from app.providers.registry import build_registry

DEMO_PASSWORD = "demo-password"
MODULES_DIR = Path(__file__).resolve().parents[2] / "infrastructure" / "terraform" / "gcp" / "modules"

configure_logging("WARNING", "console", "test")


def account_request(project: str = "acme-prod", name: str | None = None, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name or project,
        "project_id": project,
        "region": "asia-south1",
        "service_account_email": f"db-platform-provisioner@{project}.iam.gserviceaccount.com",
    }
    body.update(overrides)
    return body


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    database_url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    return Settings(
        environment="test",
        database_url=database_url,
        mock_mode=True,
        mock_speed=0,
        seed_demo_data=True,
        demo_password=DEMO_PASSWORD,
        workspaces_dir=str(tmp_path / "workspaces"),
        terraform_modules_dir=str(MODULES_DIR),
        agent_stale_seconds=90,
        bootstrap_timeout_seconds=30,
        health_timeout_seconds=30,
    )


@pytest.fixture
def database(settings: Settings) -> Iterator[SessionFactory]:
    engine = create_db_engine(settings.database_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield create_session_factory(engine)
    engine.dispose()


@pytest.fixture
def cloud_platform(settings: Settings, database: SessionFactory) -> Platform:
    """What the terraform-runner and the monitoring-worker have: cloud providers (docs/adr/0004)."""
    platform = Platform(
        settings=settings,
        session_factory=database,
        registry=build_registry(settings, database, role="all"),
        queue=InMemoryOperationQueue(),
        rate_limiter=InMemoryRateLimiter(),
        tasks=None,  # type: ignore[arg-type]
    )
    platform.tasks = inline_tasks(platform)
    return platform


@pytest.fixture
def platform(settings: Settings, database: SessionFactory, cloud_platform: Platform) -> Platform:
    """What the API and the cluster-manager have: no cloud access. Their cloud tasks run inline,
    through the same task records, on the cloud platform."""
    return Platform(
        settings=settings,
        session_factory=database,
        registry=build_registry(settings, database, role="api"),
        queue=InMemoryOperationQueue(),
        rate_limiter=InMemoryRateLimiter(),
        tasks=cloud_platform.tasks,
    )


@pytest.fixture
def client(platform: Platform) -> Iterator[TestClient]:
    with TestClient(create_app(platform=platform)) as test_client:
        yield test_client


@pytest.fixture
def login(client: TestClient) -> Callable[[str], dict[str, str]]:
    def _login(email: str, password: str = DEMO_PASSWORD) -> dict[str, str]:
        response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login


@pytest.fixture
def owner(login: Callable[[str], dict[str, str]]) -> dict[str, str]:
    return login("owner@acme.example")


@pytest.fixture
def run_worker(platform: Platform) -> Callable[[], list[str]]:
    """Run everything queued, the way a worker would, until the queue is empty."""
    runner = OperationRunner(platform, "test-worker")
    queue = platform.queue
    assert isinstance(queue, InMemoryOperationQueue)

    def _run() -> list[str]:
        processed: list[str] = []
        while items := queue.drain():
            for operation_id in items:
                runner.run(operation_id)
                processed.append(operation_id)
        return processed

    return _run


@pytest.fixture
def cloud_account(client: TestClient, owner: dict[str, str]) -> dict[str, Any]:
    response = client.post("/api/v1/cloud-accounts", json=account_request(), headers=owner)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "CONNECTED"
    return response.json()


def aws_account_request(account: str = "123456789012", name: str = "acme-aws", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "provider": "aws",
        "project_id": account,
        "region": "ap-south-1",
        "role_arn": f"arn:aws:iam::{account}:role/db-platform-provisioner",
    }
    body.update(overrides)
    return body


# Subnet IDs whose last hex digit puts them in ap-south-1a, -1b and -1c in the simulated AWS.
AWS_SUBNETS = ["subnet-0a1b2c3d4e5f60000", "subnet-0a1b2c3d4e5f60001", "subnet-0a1b2c3d4e5f60002"]


def create_environment(
    client: TestClient, headers: dict[str, str], name: str = "production", type_: str = "PRODUCTION"
) -> dict[str, Any]:
    response = client.post("/api/v1/environments", json={"name": name, "type": type_}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def register_network(
    client: TestClient,
    headers: dict[str, str],
    environment_id: str,
    account_id: str,
    *,
    name: str = "prod-network",
    region: str = "asia-south1",
    vpc: str = "prod-vpc",
    subnets: list[str] | None = None,
) -> dict[str, Any]:
    body = {
        "name": name,
        "cloud_account_id": account_id,
        "region": region,
        "vpc": vpc,
        "subnets": subnets or ["db-subnet"],
    }
    response = client.post(f"/api/v1/environments/{environment_id}/networks", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def environment(client: TestClient, owner: dict[str, str]) -> dict[str, Any]:
    return create_environment(client, owner)


@pytest.fixture
def network(
    client: TestClient, owner: dict[str, str], cloud_account: dict[str, Any], environment: dict[str, Any]
) -> dict[str, Any]:
    """A registered GCP network (prod-vpc / db-subnet in asia-south1) in the production environment."""
    created = register_network(client, owner, environment["id"], cloud_account["id"])
    assert created["status"] == "AVAILABLE", created
    return created


def cluster_request(network: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body = {
        "name": "production-search",
        "engine": "elasticsearch",
        "version": "9.5.4",
        "environment_id": network["environment_id"],
        "network_id": network["id"],
        "zone": next(iter(network["zones"]), None),
        "machine_type": "e2-standard-8",
        "node_count": 3,
        "storage_gb": 500,
        "storage_type": "pd-balanced",
        "high_availability": True,
    }
    body.update(overrides)
    return body


@pytest.fixture
def running_cluster(
    client: TestClient, owner: dict[str, str], network: dict[str, Any], run_worker: Callable[[], list[str]]
) -> dict[str, Any]:
    response = client.post("/api/v1/clusters", json=cluster_request(network), headers=owner)
    assert response.status_code == 202, response.text
    run_worker()
    detail = client.get(f"/api/v1/clusters/{response.json()['cluster_id']}", headers=owner).json()
    assert detail["lifecycle"] == "ACTIVE", detail
    return detail


def delete_cluster(client: TestClient, headers: dict[str, str], cluster: dict[str, Any]) -> Any:
    return client.delete(f"/api/v1/clusters/{cluster['id']}", params={"confirm": cluster["name"]}, headers=headers)
