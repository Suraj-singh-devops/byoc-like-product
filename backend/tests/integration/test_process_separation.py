"""Privilege-separated processes (docs/adr/0004, implementation plan P2).

The API and the cluster-manager never build cloud providers; cloud work travels as tasks through
PostgreSQL (the record) and Redis (wake-ups) to the terraform-runner and the monitoring-worker.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.application.platform import Platform
from app.application.task_handlers import build_handlers
from app.application.tasks import MONITORING, TERRAFORM, CloudTasks, RedisTaskWaker, TaskRunner
from app.config.settings import Settings
from app.domain.errors import OperationCancelled, ProvisioningError
from app.infrastructure.db import SessionFactory, session_scope
from app.models import CloudTask, Operation
from app.providers.registry import CloudAccessDenied, build_registry
from tests.conftest import account_request, cluster_request

RunWorker = Callable[[], list[str]]
BACKEND = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("role", ["api", "cluster-manager"])
def test_roles_without_cloud_access_cannot_build_providers(
    role: str, settings: Settings, database: SessionFactory
) -> None:
    registry = build_registry(settings, database, role)
    assert not registry.has_cloud_access
    assert registry.descriptor("gcp").list_regions(), "the static catalog is still there"
    with pytest.raises(CloudAccessDenied):
        registry.cloud("gcp")


@pytest.mark.parametrize("role", ["terraform-runner", "monitoring-worker"])
def test_worker_roles_have_cloud_access(role: str, settings: Settings, database: SessionFactory) -> None:
    assert build_registry(settings, database, role).cloud("aws").name == "aws"


def test_api_process_never_imports_cloud_clients() -> None:
    """Even configured for real GCP, the API process loads no provider or credential code."""
    code = (
        "import sys; from app.main import create_app, build_platform; from app.config.settings import Settings;"
        "s = Settings(service_role='api', mock_mode=False, dev_local_credentials=True,"
        " dev_allowed_projects='sandbox-a', database_url='sqlite://');"
        "p = build_platform(s); assert not p.registry.has_cloud_access;"
        "bad = [m for m in ('app.providers.cloud.gcp.provider', 'app.providers.cloud.gcp.client',"
        " 'app.providers.cloud.mock.gcp', 'google.auth.impersonated_credentials') if m in sys.modules];"
        "assert not bad, bad"
    )
    env = {**os.environ, "SECRET_KEY": "x" * 40}
    # A fixed script run with this interpreter.
    result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True)  # noqa: S603
    assert result.returncode == 0, result.stderr[-2000:]


def test_workflows_run_without_cloud_access_and_hand_work_to_the_right_worker(
    client: TestClient,
    owner: dict[str, str],
    network: dict[str, Any],
    platform: Platform,
    run_worker: RunWorker,
) -> None:
    assert not platform.registry.has_cloud_access, "the cluster-manager of the tests has no cloud access"
    created = client.post("/api/v1/clusters", json=cluster_request(network), headers=owner).json()
    run_worker()
    op = client.get(f"/api/v1/operations/{created['operation_id']}", headers=owner).json()
    assert op["status"] == "COMPLETED", op["error"]
    assert any("Creating VM" in e["message"] for e in op["log"]), "the runner's progress reaches the operation log"
    with session_scope(platform.session_factory) as s:
        tasks = s.scalars(select(CloudTask).where(CloudTask.operation_id == uuid.UUID(created["operation_id"])))
        by_role: dict[str, set[str]] = {}
        for task in tasks:
            assert task.status == "SUCCEEDED"
            by_role.setdefault(task.role, set()).add(task.kind)
    assert by_role[TERRAFORM] == {"preflight", "plan", "apply"}
    assert by_role[MONITORING] == {"refresh_cluster"}


# --------------------------------------------------------------- the protocol


@pytest.fixture
def redis_waker() -> RedisTaskWaker:
    return RedisTaskWaker(fakeredis.FakeRedis())


def worker(cloud_platform: Platform, role: str, name: str = "w1") -> TaskRunner:
    return TaskRunner(cloud_platform.session_factory, build_handlers(cloud_platform), {role}, name)


def org_and_account(client: TestClient, owner: dict[str, str]) -> tuple[uuid.UUID, dict[str, Any]]:
    account = client.post("/api/v1/cloud-accounts", json=account_request(), headers=owner).json()
    org = client.get("/api/v1/auth/me", headers=owner).json()["organization"]["id"]
    return uuid.UUID(org), account


def test_task_travels_through_redis_to_a_worker_of_its_role(
    client: TestClient, owner: dict[str, str], cloud_platform: Platform, redis_waker: RedisTaskWaker
) -> None:
    org, account = org_and_account(client, owner)
    tasks = CloudTasks(cloud_platform.session_factory, redis_waker, poll_interval=0.05)
    monitoring, terraform = worker(cloud_platform, MONITORING), worker(cloud_platform, TERRAFORM)

    task_id = tasks.submit("validate_account", {"account_id": account["id"]}, organization_id=org)
    woken = redis_waker.wait(TERRAFORM, timeout=1)
    assert woken == str(task_id)
    assert not monitoring.run(task_id), "a monitoring-worker never runs terraform tasks"
    assert terraform.run(task_id)
    assert not terraform.run(task_id), "claimed once only"
    assert tasks.wait(task_id, timeout=5) == {"status": "CONNECTED", "valid": True}


def test_a_lost_wake_up_only_delays_the_task(
    client: TestClient, owner: dict[str, str], cloud_platform: Platform
) -> None:
    org, account = org_and_account(client, owner)
    tasks = CloudTasks(cloud_platform.session_factory, waker=None, poll_interval=0.05)
    task_id = tasks.submit("validate_account", {"account_id": account["id"]}, organization_id=org)
    runner = worker(cloud_platform, TERRAFORM)
    thread = threading.Thread(target=lambda: runner.run(runner.next_pending()))  # type: ignore[arg-type]
    thread.start()
    assert tasks.wait(task_id, timeout=10)["valid"] is True
    thread.join()


def test_a_task_whose_worker_stopped_is_abandoned(
    client: TestClient, owner: dict[str, str], cloud_platform: Platform
) -> None:
    org, account = org_and_account(client, owner)
    tasks = CloudTasks(cloud_platform.session_factory, poll_interval=0.01, stale_after_seconds=60)
    task_id = tasks.submit("validate_account", {"account_id": account["id"]}, organization_id=org)
    assert worker(cloud_platform, TERRAFORM).claim(task_id)
    with session_scope(cloud_platform.session_factory) as s:
        task = s.get(CloudTask, task_id)
        assert task is not None
        task.heartbeat_at = datetime.now(UTC) - timedelta(minutes=5)
    with pytest.raises(ProvisioningError) as excinfo:
        tasks.wait(task_id, timeout=5)
    assert excinfo.value.code == "TASK_ABANDONED"


def test_cancelling_the_operation_stops_its_running_task(
    client: TestClient,
    owner: dict[str, str],
    network: dict[str, Any],
    cloud_platform: Platform,
    platform: Platform,
) -> None:
    created = client.post("/api/v1/clusters", json=cluster_request(network), headers=owner).json()
    operation_id = uuid.UUID(created["operation_id"])
    with session_scope(platform.session_factory) as s:
        op = s.get(Operation, operation_id)
        assert op is not None
        op.cancel_requested = True
        org = op.organization_id
    tasks = CloudTasks(cloud_platform.session_factory, poll_interval=0.01)
    task_id = tasks.submit(
        "refresh_cluster", {"cluster_id": created["cluster_id"]}, organization_id=org, operation_id=operation_id
    )
    runner = worker(cloud_platform, MONITORING)

    def slow_refresh(task: Any) -> dict[str, Any]:
        task.progress._last_cancel_check = 0.0
        task.progress.sleep(5)
        return {}

    runner.handlers = {**runner.handlers, "refresh_cluster": slow_refresh}
    runner.run(task_id)
    with pytest.raises(OperationCancelled):
        tasks.wait(task_id, timeout=5)


def test_account_validation_is_asynchronous(
    client: TestClient, owner: dict[str, str], platform: Platform, cloud_platform: Platform
) -> None:
    """Without an inline runner the API answers at once, VALIDATING; the runner finishes the job."""
    inline = platform.tasks
    platform.tasks = CloudTasks(platform.session_factory, waker=None)
    try:
        account = client.post("/api/v1/cloud-accounts", json=account_request(), headers=owner).json()
        assert account["status"] == "VALIDATING"
        runner = worker(cloud_platform, TERRAFORM)
        assert runner.run(runner.next_pending())  # type: ignore[arg-type]
        after = client.get(f"/api/v1/cloud-accounts/{account['id']}", headers=owner).json()
        assert after["status"] == "CONNECTED"
        audit = client.get("/api/v1/audit-logs?action=CLOUD_ACCOUNT_VALIDATED", headers=owner).json()["items"][0]
        assert audit["user"] == "owner@acme.example", "the runner audits on behalf of the requester"
    finally:
        platform.tasks = inline


def test_tasks_of_another_organization_are_refused(
    client: TestClient, owner: dict[str, str], login: Callable[[str], dict[str, str]], cloud_platform: Platform
) -> None:
    _, account = org_and_account(client, owner)
    globex = uuid.UUID(
        client.get("/api/v1/auth/me", headers=login("owner@globex.example")).json()["organization"]["id"]
    )
    tasks = CloudTasks(cloud_platform.session_factory, poll_interval=0.01)
    task_id = tasks.submit("validate_account", {"account_id": account["id"]}, organization_id=globex)
    worker(cloud_platform, TERRAFORM).run(task_id)
    with pytest.raises(ProvisioningError) as excinfo:
        tasks.wait(task_id, timeout=5)
    assert excinfo.value.code == "CLOUD_ACCOUNT_MISSING"
