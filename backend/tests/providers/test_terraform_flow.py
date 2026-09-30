"""GCPProvider's Terraform flow against a fake ``tofu`` binary (no cloud, no real Terraform)."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import Settings
from app.domain.errors import CloudProviderError, ProvisioningError
from app.infrastructure.terraform_runner import TerraformRunner
from app.providers.cloud.gcp.provider import GCPProvider
from tests.providers.test_gcp_provider import ACCOUNT, FakeClient, request

FAKE_TOFU = r"""#!PYTHON
import json, os, sys
from pathlib import Path

args = [a for a in sys.argv[1:] if a != "-no-color"]
cmd = args[0]
# Configuration comes from a file: the runner hands Terraform only a minimal environment.
config = json.loads(Path(__file__).with_name("fake.json").read_text())
with Path(config["log"]).open("a") as fh:
    fh.write(json.dumps({"args": args, "env": sorted(os.environ)}) + "\n")
fail = config.get("fail")
plan_actions = config.get("plan", [])

def event(kind, **extra):
    print(json.dumps({"@level": "info", "@message": kind, "type": kind, **extra}), flush=True)

if cmd == "init":
    print("Initializing the backend...")
    sys.exit(0)
if cmd == "plan":
    if fail == "plan":
        event("diagnostic", diagnostic={"severity": "error", "summary": "Error 403: Required 'compute.networks.create' permission for 'projects/customer-prod/global/networks/x'", "detail": ""})
        sys.exit(1)
    Path("tfplan").write_text(json.dumps({"resource_changes": plan_actions}))
    event("change_summary", changes={"add": len(plan_actions)})
    sys.exit(0)
if cmd == "show":
    print(Path(args[-1]).read_text())
    sys.exit(0)
if cmd == "apply":
    changes = json.loads(Path(args[-1]).read_text())["resource_changes"]
    for rc in changes:
        event("apply_start", hook={"resource": {"addr": rc["address"]}, "action": "create"})
        if fail == "apply":
            event("diagnostic", diagnostic={"severity": "error", "summary": "Error creating instance: googleapi: Error 403: Quota 'CPUS' exceeded.  Limit: 8.0 in region asia-south1.", "detail": ""})
            sys.exit(1)
        event("apply_complete", hook={"resource": {"addr": rc["address"]}, "action": "create", "elapsed_seconds": 1})
    Path("terraform.tfstate").write_text("{}")
    sys.exit(0)
if cmd == "output":
    print(json.dumps({
        "nodes": {"value": {
            "node-1": {"instance_name": "production-search-0b8c-node-1", "instance_id": "111", "zone": "asia-south1-a", "private_ip": "10.10.0.2", "hostname": "h1"},
            "node-2": {"instance_name": "production-search-0b8c-node-2", "instance_id": "222", "zone": "asia-south1-b", "private_ip": "10.10.0.3", "hostname": "h2"}
        }},
        "network": {"value": "projects/customer-prod/global/networks/production-search-0b8c-vpc"}
    }))
    sys.exit(0)
sys.exit(2)
"""


class Progress:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def message(self, text: str) -> None:
        self.messages.append(text)

    def heartbeat(self) -> None:
        pass

    def check_cancelled(self) -> None:
        pass

    def sleep(self, seconds: float) -> None:
        pass


def configure(binary: Path, **values: Any) -> None:
    config_file = binary.with_name("fake.json")
    config = json.loads(config_file.read_text())
    config.update(values)
    config_file.write_text(json.dumps(config))


@pytest.fixture
def fake_tofu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binary = tmp_path / "bin" / "tofu"
    binary.parent.mkdir()
    binary.write_text(FAKE_TOFU.replace("#!PYTHON", f"#!{sys.executable}"))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    binary.with_name("fake.json").write_text(json.dumps({"log": str(tmp_path / "calls.jsonl")}))
    # Secrets of the control plane itself must never reach Terraform.
    monkeypatch.setenv("SECRET_KEY", "control-plane-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@db/byoc")
    return binary


def gcp(tmp_path: Path, binary: Path) -> GCPProvider:
    modules = tmp_path / "modules" / "elasticsearch"
    modules.mkdir(parents=True, exist_ok=True)
    (modules / "main.tf").write_text("# stub")
    settings = Settings(
        environment="test",
        terraform_binary=str(binary),
        terraform_modules_dir=str(tmp_path / "modules"),
        workspaces_dir=str(tmp_path / "workspaces"),
        agent_binaries_dir=str(tmp_path / "no-agent"),
    )
    return GCPProvider(settings, client_factory=lambda _a: FakeClient())  # type: ignore[arg-type,return-value]


def calls(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]


def plan_changes(binary: Path, *changes: tuple[str, list[str]]) -> None:
    configure(binary, plan=[{"address": a, "change": {"actions": acts}} for a, acts in changes])


def test_plan_apply_outputs(tmp_path: Path, fake_tofu: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan_changes(
        fake_tofu,
        ("module.cluster.module.network.google_compute_network.this[0]", ["create"]),
        ('module.cluster.module.compute.google_compute_instance.node["node-1"]', ["create"]),
        ('module.cluster.module.compute.google_compute_instance.node["node-2"]', ["create"]),
    )
    provider = gcp(tmp_path, fake_tofu)
    progress = Progress()
    summary = provider.plan_infrastructure(ACCOUNT, request(), progress)
    assert summary.to_add == 3
    state = provider.apply_infrastructure(ACCOUNT, request(), progress)
    assert [n.name for n in state.nodes] == ["node-1", "node-2"]
    assert state.nodes[1].private_ip == "10.10.0.3"
    assert any("Applied 3/3 resources" in m for m in progress.messages)

    workspace = tmp_path / "workspaces" / request().cluster_id
    root = json.loads((workspace / "main.tf.json").read_text())
    assert root["module"]["cluster"]["source"] == "./modules/elasticsearch"
    assert not (workspace / "tfplan").exists(), "plan files hold secrets and are removed after apply"

    recorded = calls(tmp_path)
    assert [c["args"][0] for c in recorded] == ["init", "plan", "show", "show", "apply", "output"]
    for call in recorded:
        assert "SECRET_KEY" not in call["env"]
        assert "DATABASE_URL" not in call["env"]
        assert "GOOGLE_CREDENTIALS" not in call["env"], "no key material ever reaches Terraform"
        assert "GOOGLE_IMPERSONATE_SERVICE_ACCOUNT" in call["env"]
        assert "TF_ENCRYPTION" in call["env"]


def test_permission_error_in_plan(tmp_path: Path, fake_tofu: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(fake_tofu, fail="plan")
    with pytest.raises(CloudProviderError) as excinfo:
        gcp(tmp_path, fake_tofu).plan_infrastructure(ACCOUNT, request(), Progress())
    assert excinfo.value.code == "GCP_PERMISSION_DENIED"
    assert excinfo.value.reason == "Service account does not have compute.networks.create permission."


def test_quota_error_in_apply(tmp_path: Path, fake_tofu: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan_changes(fake_tofu, ('module.cluster.module.compute.google_compute_instance.node["node-1"]', ["create"]))
    provider = gcp(tmp_path, fake_tofu)
    provider.plan_infrastructure(ACCOUNT, request(), Progress())
    configure(fake_tofu, fail="apply")
    with pytest.raises(CloudProviderError) as excinfo:
        provider.apply_infrastructure(ACCOUNT, request(), Progress())
    assert excinfo.value.code == "GCP_QUOTA_EXCEEDED"


def test_unsafe_plan_is_refused_before_apply(tmp_path: Path, fake_tofu: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan_changes(fake_tofu, ('module.cluster.module.compute.google_compute_disk.data["node-1"]', ["delete", "create"]))
    provider = gcp(tmp_path, fake_tofu)
    with pytest.raises(ProvisioningError) as excinfo:
        provider.plan_infrastructure(ACCOUNT, request(), Progress())
    assert excinfo.value.code == "UNSAFE_PLAN"
    assert not (tmp_path / "workspaces" / request().cluster_id / "tfplan").exists()


def test_destroy_removes_local_workspace(tmp_path: Path, fake_tofu: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan_changes(fake_tofu, ('module.cluster.module.compute.google_compute_instance.node["node-1"]', ["delete"]))
    provider = gcp(tmp_path, fake_tofu)
    provider.destroy_infrastructure(ACCOUNT, request(), Progress())
    assert not (tmp_path / "workspaces" / request().cluster_id).exists()
    assert "-destroy" in calls(tmp_path)[1]["args"]


def test_runner_timeout(tmp_path: Path) -> None:
    script = tmp_path / "slow"
    script.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    from app.infrastructure.terraform_runner import TerraformTimeout

    with pytest.raises(TerraformTimeout):
        TerraformRunner(str(script)).run(tmp_path, ["plan"], {}, timeout=0.5)


def test_runner_is_opentofu_by_name() -> None:
    assert TerraformRunner("/usr/local/bin/tofu").is_opentofu
    assert not TerraformRunner("terraform").is_opentofu


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_runner_captures_text_errors(tmp_path: Path) -> None:
    script = tmp_path / "failing"
    script.write_text(
        f"#!{sys.executable}\nprint('Error: Failed to query available provider packages')\nraise SystemExit(1)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    result = TerraformRunner(str(script)).init(tmp_path, {})
    assert not result.ok
    assert "Failed to query available provider packages" in result.diagnostics[0]["summary"]
