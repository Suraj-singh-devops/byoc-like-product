from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.domain.errors import PlatformError, ProvisioningError, ValidationFailed
from app.infrastructure.logging import get_logger
from app.infrastructure.metrics import CLOUD_API_FAILURES
from app.infrastructure.security import derive_key
from app.infrastructure.terraform_runner import TerraformResult, TerraformRunner, TerraformTimeout
from app.providers.cloud.base import (
    CloudAccountContext,
    CloudProvider,
    CredentialValidationResult,
    GuestReport,
    InfrastructureRequest,
    InfrastructureState,
    MachineType,
    NetworkDetails,
    NetworkLookup,
    NodeRef,
    PlanSummary,
    ProgressReporter,
    ProvisionedNode,
    ResourceChange,
    ValidationCheck,
)
from app.providers.cloud.gcp.catalog import architecture_for, is_supported_machine
from app.providers.cloud.gcp.client import GcpApiClient
from app.providers.cloud.gcp.descriptor import GcpDescriptor
from app.providers.cloud.gcp.errors import from_terraform_diagnostics, permission_denied
from app.providers.cloud.gcp.network import gcp_network_details
from app.providers.cloud.gcp.permissions import REQUIRED_APIS, REQUIRED_PERMISSIONS
from app.providers.cloud.gcp.terraform import (
    PLAN_FILE,
    AgentArtifact,
    backend_config,
    module_variables,
    prepare_workspace,
    render_root_module,
    state_encryption_env,
)

log = get_logger(__name__)

STATEFUL_RESOURCE_TYPES = ("google_compute_instance", "google_compute_disk")
_FOR_EACH_KEY = re.compile(r'\["([^"]+)"\]$')


def summarize_plan(plan: dict[str, Any]) -> PlanSummary:
    summary = PlanSummary()
    for rc in plan.get("resource_changes", []) or []:
        actions = list((rc.get("change") or {}).get("actions", []))
        if not actions or actions in (["no-op"], ["read"]):
            continue
        summary.changes.append(ResourceChange(rc.get("address", "?"), actions))
        if "create" in actions:
            summary.to_add += 1
        if "delete" in actions:
            summary.to_destroy += 1
        if actions == ["update"]:
            summary.to_change += 1
    return summary


def resource_type(address: str) -> str:
    """``module.cluster.module.compute.google_compute_instance.node["node-2"]`` -> type."""
    parts = _FOR_EACH_KEY.sub("", address).split(".")
    return parts[-2] if len(parts) >= 2 else address


def guard_plan(summary: PlanSummary) -> None:
    """Never let create or scale destroy or replace a VM or disk holding data."""
    for change in summary.changes:
        if "delete" not in change.actions or resource_type(change.address) not in STATEFUL_RESOURCE_TYPES:
            continue
        verb = "replaced" if "create" in change.actions else "destroyed"
        raise ProvisioningError(
            "Refusing to apply: the plan would destroy data on an existing node.",
            code="UNSAFE_PLAN",
            reason=f"{change.address} would be {verb}.",
            suggested_action=(
                "Nothing was changed. Check the cloud project for manual changes (drift) to this "
                "cluster's resources, then retry."
            ),
            details={"address": change.address, "actions": change.actions},
        )


def parse_outputs(outputs: dict[str, Any]) -> InfrastructureState:
    nodes = [
        ProvisionedNode(
            name=name,
            instance_name=str(value["instance_name"]),
            instance_id=str(value["instance_id"]),
            zone=str(value["zone"]),
            private_ip=str(value["private_ip"]),
            hostname=str(value.get("hostname") or ""),
        )
        for name, value in sorted((outputs.get("nodes") or {}).items())
    ]
    extra = {k: v for k, v in outputs.items() if k != "nodes"}
    return InfrastructureState(nodes=nodes, outputs=extra)


class _ApplyProgress:
    def __init__(self, progress: ProgressReporter, total: int, verb: str) -> None:
        self.progress = progress
        self.total = max(total, 1)
        self.verb = verb
        self.done = 0

    def __call__(self, event: dict[str, Any]) -> None:
        self.progress.heartbeat()
        kind = event.get("type")
        hook = event.get("hook") or {}
        address = (hook.get("resource") or {}).get("addr", "")
        short = address.replace("module.cluster.", "")
        if kind == "apply_complete":
            self.done += 1
            self.progress.message(f"{self.verb} {self.done}/{self.total} resources ({short})")
        elif kind == "apply_start" and self.done == 0:
            self.progress.message(f"{self.verb} resources: {short}")


class GCPProvider(CloudProvider):
    def __init__(
        self,
        settings: Settings,
        *,
        runner: TerraformRunner | None = None,
        client_factory: Callable[[CloudAccountContext], GcpApiClient] | None = None,
    ) -> None:
        self.settings = settings
        self.descriptor = GcpDescriptor(settings, simulated=False)
        self.runner = runner or TerraformRunner(
            settings.terraform_binary,
            timeout_seconds=settings.provision_timeout_seconds,
            plugin_cache_dir=settings.terraform_plugin_cache_dir,
        )
        allowed = settings.dev_projects if settings.dev_local_credentials else None
        self._client_factory = client_factory or (
            lambda account: GcpApiClient(account, settings.gcp_api_timeout_seconds, allowed)
        )

    def client(self, account: CloudAccountContext) -> GcpApiClient:
        return self._client_factory(account)

    # ---------------------------------------------------------------- validation

    def validate_credentials(self, account: CloudAccountContext) -> CredentialValidationResult:
        checks: list[ValidationCheck] = []
        errors: list[PlatformError] = []

        def fail(key: str, name: str, err: PlatformError) -> None:
            checks.append(ValidationCheck(key, name, "failed", str(err)))
            errors.append(err)

        try:
            client = self.client(account)
            client.ensure_token()
            checks.append(
                ValidationCheck("credentials", "Credentials", "passed", f"Authenticated as {client.principal}")
            )
        except PlatformError as err:
            fail("credentials", "Credentials", err)
            return self._result(checks, errors, [])

        try:
            project = client.get_project()
            state = project.get("lifecycleState", "ACTIVE")
            if state != "ACTIVE":
                raise ValidationFailed(
                    f"Project {account.project_id} is {state}.",
                    suggested_action="Use an active project.",
                )
            checks.append(
                ValidationCheck("project", "Project access", "passed", f"Project {account.project_id} is active")
            )
        except PlatformError as err:
            fail("project", "Project access", err)
            return self._result(checks, errors, [])

        for service, label in REQUIRED_APIS.items():
            try:
                client.probe_service(service)
                checks.append(ValidationCheck(f"api:{service}", label, "passed", "Enabled"))
            except PlatformError as err:
                fail(f"api:{service}", label, err)

        missing: list[str] = []
        try:
            granted = client.test_permissions(REQUIRED_PERMISSIONS)
            missing = sorted(set(REQUIRED_PERMISSIONS) - granted)
            if missing:
                err = permission_denied(missing[0], account.project_id, client.principal)
                err.details["missing_permissions"] = missing
                fail("permissions", "IAM permissions", err)
            else:
                checks.append(
                    ValidationCheck(
                        "permissions",
                        "IAM permissions",
                        "passed",
                        f"All {len(REQUIRED_PERMISSIONS)} permissions granted",
                    )
                )
        except PlatformError as err:
            fail("permissions", "IAM permissions", err)
        return self._result(checks, errors, missing)

    @staticmethod
    def _result(
        checks: list[ValidationCheck], errors: list[PlatformError], missing: list[str]
    ) -> CredentialValidationResult:
        return CredentialValidationResult(
            valid=not errors,
            checks=checks,
            missing_permissions=missing,
            error=errors[0].to_dict() if errors else None,
        )

    # ------------------------------------------------------------------ catalog

    def list_machine_types(self, account: CloudAccountContext, zone: str) -> list[MachineType]:
        machines = self.client(account).list_machine_types(zone)
        return sorted(
            (m for m in machines if is_supported_machine(m.name, m.vcpus, m.memory_gb)),
            key=lambda m: (m.name.split("-")[0], m.vcpus, m.memory_gb),
        )

    def validate_placement(
        self, account: CloudAccountContext, region: str, zone: str, machine_type: str
    ) -> MachineType:
        client = self.client(account)
        zone_info = client.get_zone(zone)
        if zone_info is None or zone_info.get("region", "").rsplit("/", 1)[-1] != region:
            raise ValidationFailed(
                f"Zone {zone} does not exist in region {region} for this project.",
                details={"fields": {"zone": "Unknown zone for this region."}},
            )
        if zone_info.get("status") != "UP":
            raise ValidationFailed(f"Zone {zone} is currently {zone_info.get('status')}.")
        machine = client.get_machine_type(zone, machine_type)
        if machine is None:
            raise ValidationFailed(
                f"Machine type {machine_type} is not available in {zone}.",
                details={"fields": {"machine_type": "Not available in this zone."}},
            )
        if not is_supported_machine(machine.name, machine.vcpus, machine.memory_gb):
            raise ValidationFailed(
                f"Machine type {machine_type} is not supported.",
                reason="Supported families are e2, n2, n2d and t2a with at least 2 vCPUs and 4 GB memory "
                "(they support Persistent Disk).",
                details={"fields": {"machine_type": "Unsupported machine family."}},
            )
        return machine

    # ------------------------------------------------------------------ networks

    def describe_network(self, account: CloudAccountContext, lookup: NetworkLookup) -> NetworkDetails:
        # Read-only calls. From P4 they run as the customer's monitor service account (docs/adr/0013).
        client = self.client(account)
        region = next((r for r in client.list_regions() if r.name == lookup.region), None)
        return gcp_network_details(
            project=account.project_id,
            lookup=lookup,
            zones=region.zones if region else (),
            network=client.get_network(lookup.vpc),
            subnet=client.get_subnetwork(lookup.region, lookup.subnets[0]),
            routers=client.list_routers(lookup.region),
        )

    # ------------------------------------------------------------------ terraform

    def _agent_artifact(self, machine_type: str) -> AgentArtifact | None:
        arch = "arm64" if architecture_for(machine_type) == "arm64" else "amd64"
        path = Path(self.settings.agent_binaries_dir) / f"byoc-agent-linux-{arch}"
        if not path.is_file():
            log.warning("agent_binary_missing", path=str(path))
            return None
        return AgentArtifact(str(path.resolve()), self.settings.agent_version)

    def _workspace(self, account: CloudAccountContext, request: InfrastructureRequest) -> Path:
        variables = module_variables(
            request,
            agent=self._agent_artifact(request.machine_type),
            control_plane_url=self.settings.control_plane_public_url,
            agent_audience=self.settings.agent_identity_audience,
        )
        root = render_root_module(request, variables, backend_config(request))
        return prepare_workspace(self.settings.workspaces_dir, self.settings.terraform_modules_dir, request, root)

    def _env(self, account: CloudAccountContext, request: InfrastructureRequest) -> dict[str, str]:
        # Keyless: the provider impersonates the customer's service account with the runner's
        # own credentials (Workload Identity on GKE). No key material ever reaches Terraform.
        if self.settings.dev_local_credentials:
            if account.project_id not in self.settings.dev_projects:
                raise ProvisioningError(
                    f"Project {account.project_id} is not in DEV_ALLOWED_PROJECTS.", code="PROJECT_NOT_ALLOWED"
                )
            # Development only (docs/adr/0015): the developer's own credentials, quota billed to the project.
            env = {
                "GOOGLE_PROJECT": account.project_id,
                "GOOGLE_BILLING_PROJECT": account.project_id,
                "USER_PROJECT_OVERRIDE": "true",
            }
        else:
            env = {
                "GOOGLE_PROJECT": account.project_id,
                "GOOGLE_IMPERSONATE_SERVICE_ACCOUNT": account.service_account_email or "",
            }
        if self.settings.terraform_state_encryption and self.runner.is_opentofu:
            env.update(state_encryption_env(derive_key(self.settings.secret_key, f"tfstate:{request.cluster_id}")))
        return env

    def _check(self, result: TerraformResult, stage: str, account: CloudAccountContext) -> None:
        if result.ok or (stage == "plan" and result.returncode == 2):
            return
        CLOUD_API_FAILURES.labels("gcp", f"terraform_{stage}").inc()
        log.error("terraform_failed", stage=stage, output_tail=result.output_tail[-30:])
        raise from_terraform_diagnostics(result.diagnostics, project=account.project_id, stage=stage)

    def _run(self, fn: Callable[[], TerraformResult], stage: str) -> TerraformResult:
        try:
            return fn()
        except TerraformTimeout as exc:
            raise ProvisioningError(
                f"Terraform {stage} timed out.",
                code="TERRAFORM_TIMEOUT",
                reason=str(exc),
                suggested_action="Retry the operation; Terraform resumes from its saved state.",
            ) from exc

    def plan_infrastructure(
        self, account: CloudAccountContext, request: InfrastructureRequest, progress: ProgressReporter
    ) -> PlanSummary:
        workspace = self._workspace(account, request)
        env = self._env(account, request)
        progress.message("Initializing Terraform (providers and state backend)")
        self._check(self._run(lambda: self.runner.init(workspace, env), "init"), "init", account)
        progress.message("Planning infrastructure changes")
        result = self._run(
            lambda: self.runner.plan(workspace, env, PLAN_FILE, on_event=lambda _e: progress.heartbeat()),
            "plan",
        )
        self._check(result, "plan", account)
        summary = summarize_plan(self.runner.show_plan(workspace, env, PLAN_FILE))
        try:
            guard_plan(summary)
        except ProvisioningError:
            (workspace / PLAN_FILE).unlink(missing_ok=True)
            raise
        return summary

    def apply_infrastructure(
        self, account: CloudAccountContext, request: InfrastructureRequest, progress: ProgressReporter
    ) -> InfrastructureState:
        workspace = Path(self.settings.workspaces_dir) / request.cluster_id
        if not (workspace / PLAN_FILE).exists():
            self.plan_infrastructure(account, request, progress)
        env = self._env(account, request)
        total = len(summarize_plan(self.runner.show_plan(workspace, env, PLAN_FILE)).changes)
        tracker = _ApplyProgress(progress, total, "Applied")
        try:
            result = self._run(lambda: self.runner.apply(workspace, env, PLAN_FILE, on_event=tracker), "apply")
        finally:
            (workspace / PLAN_FILE).unlink(missing_ok=True)
        self._check(result, "apply", account)
        return parse_outputs(self.runner.output(workspace, env))

    def destroy_infrastructure(
        self, account: CloudAccountContext, request: InfrastructureRequest, progress: ProgressReporter
    ) -> None:
        workspace = self._workspace(account, request)
        env = self._env(account, request)
        progress.message("Initializing Terraform")
        self._check(self._run(lambda: self.runner.init(workspace, env), "init"), "init", account)
        progress.message("Planning destruction of all cluster resources")
        result = self._run(lambda: self.runner.plan(workspace, env, PLAN_FILE, destroy=True), "plan")
        self._check(result, "plan", account)
        total = len(summarize_plan(self.runner.show_plan(workspace, env, PLAN_FILE)).changes)
        tracker = _ApplyProgress(progress, total, "Destroyed")
        try:
            result = self._run(lambda: self.runner.apply(workspace, env, PLAN_FILE, on_event=tracker), "destroy")
        finally:
            (workspace / PLAN_FILE).unlink(missing_ok=True)
        self._check(result, "destroy", account)
        # Local state of a destroyed cluster is no longer needed and holds sensitive values.
        shutil.rmtree(workspace, ignore_errors=True)

    # ------------------------------------------------------------------ status

    def get_resource_status(self, account: CloudAccountContext, nodes: list[NodeRef]) -> dict[str, str]:
        client = self.client(account)
        statuses: dict[str, str] = {}
        for node in nodes:
            instance = client.get_instance(node.zone, node.instance_name)
            statuses[node.name] = "NOT_FOUND" if instance is None else str(instance.get("status", "UNKNOWN"))
        return statuses

    def read_node_reports(self, account: CloudAccountContext, nodes: list[NodeRef]) -> dict[str, GuestReport]:
        client = self.client(account)
        reports: dict[str, GuestReport] = {}
        for node in nodes:
            values = client.guest_attributes(node.zone, node.instance_name)
            bootstrap = values.get("bootstrap")
            report = values.get("report")
            reports[node.name] = GuestReport(
                node_name=node.name,
                bootstrap=bootstrap if isinstance(bootstrap, dict) else None,
                report=report if isinstance(report, dict) else None,
            )
        return reports
