"""The cloud work behind each task kind (app/application/tasks.py).

Only the terraform-runner (validate_account, preflight, plan, apply, destroy) and the
monitoring-worker (describe_network, validate_network, refresh_cluster) run these, because only
they have cloud providers (docs/adr/0004). Every handler re-reads its records from the database
and checks they belong to the task's organization.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.application.audit_service import record_audit
from app.application.cloud_account_service import build_account_context
from app.application.health_service import HealthService
from app.application.platform import Platform
from app.application.tasks import MONITORING, TERRAFORM, CloudTasks, Handler, TaskContext, TaskRunner
from app.domain.enums import AuditEvent, AuditStatus
from app.domain.errors import CloudProviderError, PlatformError, ProvisioningError
from app.domain.network import NetworkRef
from app.domain.states import CloudAccountStatus, NetworkStatus
from app.infrastructure.db import session_scope
from app.models import CloudAccount, Cluster, Network, User
from app.providers.cloud.base import (
    CloudAccountContext,
    CloudProvider,
    InfrastructureRequest,
    NetworkDetails,
    NetworkLookup,
    NodePlacement,
    ProgressReporter,
    ValidationCheck,
)
from app.repositories import queries


def utcnow() -> datetime:
    return datetime.now(UTC)


# -------------------------------------------------------------- serialization


def request_to_dict(request: InfrastructureRequest) -> dict[str, Any]:
    return asdict(request)


def request_from_dict(data: dict[str, Any]) -> InfrastructureRequest:
    values = dict(data)
    values["nodes"] = [
        NodePlacement(name=n["name"], ordinal=int(n["ordinal"]), zone=n["zone"], roles=tuple(n["roles"]))
        for n in data["nodes"]
    ]
    values["network"] = NetworkRef.from_doc(data["network"]) if data.get("network") else None
    return InfrastructureRequest(**values)


def lookup_of(ref: NetworkRef, region: str) -> NetworkLookup:
    """The identifiers to look a cluster's network up again (GCP paths end in the resource name)."""
    return NetworkLookup(
        region=region,
        vpc=ref.vpc.rsplit("/", 1)[-1],
        subnets=tuple(s.id.rsplit("/", 1)[-1] for s in ref.subnets),
    )


def failed_lookup(lookup: NetworkLookup, err: PlatformError) -> NetworkDetails:
    return NetworkDetails(
        valid=False,
        region=lookup.region,
        vpc=lookup.vpc,
        vpc_name=lookup.vpc,
        vpc_cidrs=[],
        subnets=[],
        zones=[],
        checks=[ValidationCheck("lookup", "Network lookup", "failed", str(err))],
        error=err.to_dict(),
    )


# ------------------------------------------------------------ shared checks


def require_access(cloud: CloudProvider, account: CloudAccountContext) -> None:
    result = cloud.validate_credentials(account)
    if not result.valid:
        error = result.error or {}
        raise CloudProviderError(
            error.get("message", "Cloud access could not be verified."),
            code=error.get("code", "CLOUD_VALIDATION_FAILED"),
            reason=error.get("reason"),
            suggested_action=error.get("suggested_action"),
            details=error.get("details"),
        )


def verify_network(
    cloud: CloudProvider, account: CloudAccountContext, network: NetworkRef, region: str, progress: ProgressReporter
) -> None:
    """Re-check a cluster's registered network before changing anything (docs/adr/0013)."""
    details = cloud.describe_network(account, lookup_of(network, region))
    if not details.valid:
        error = details.error or {}
        raise CloudProviderError(
            f"Network {network.name} is no longer usable: {error.get('message', 'the lookup failed')}",
            code="NETWORK_UNAVAILABLE",
            reason=error.get("reason"),
            suggested_action="Fix the network in the cloud account, validate it again, then retry.",
        )
    found = {s.id: s.cidr for s in details.subnets}
    for subnet in network.subnets:
        if found.get(subnet.id) != subnet.cidr:
            raise CloudProviderError(
                f"Subnet {subnet.id} changed since the cluster was created.",
                code="NETWORK_CHANGED",
                reason=f"Expected range {subnet.cidr}, found {found.get(subnet.id) or 'no subnet'}.",
                suggested_action="Restore the subnet, or contact support to move the cluster.",
            )
    progress.message(f"Network {network.name} verified: {', '.join(network.cidrs)}")


# ------------------------------------------------------------------- handlers


def build_handlers(platform: Platform) -> dict[str, Handler]:
    registry = platform.registry
    sf = platform.session_factory
    health = HealthService(platform)

    def account_of(s: Session, task: TaskContext, account_id: Any) -> CloudAccount:
        account = s.get(CloudAccount, uuid.UUID(str(account_id)))
        if account is None or account.organization_id != task.organization_id:
            raise ProvisioningError(
                "The cloud account was removed.",
                code="CLOUD_ACCOUNT_MISSING",
                suggested_action="Re-add the cloud account for this project.",
            )
        return account

    def access(task: TaskContext) -> tuple[CloudProvider, CloudAccountContext]:
        with session_scope(sf) as s:
            account = account_of(s, task, task.payload["account_id"])
            return registry.cloud(account.provider), build_account_context(account)

    def requester(s: Session, task: TaskContext) -> str | None:
        user = s.get(User, task.requested_by_id) if task.requested_by_id else None
        return user.email if user else None

    # -------------------------------------------------------- terraform-runner

    def validate_account(task: TaskContext) -> dict[str, Any]:
        cloud, account_ctx = access(task)
        result = cloud.validate_credentials(account_ctx)
        with session_scope(sf) as s:
            account = account_of(s, task, task.payload["account_id"])
            now = utcnow()
            if result.valid:
                account.status = CloudAccountStatus.CONNECTED.value
                account.last_connected_at = now
            elif account.last_connected_at is not None:
                account.status = CloudAccountStatus.DISCONNECTED.value
            else:
                account.status = CloudAccountStatus.FAILED.value
            account.validation_result = result.to_dict()
            account.last_validated_at = now
            record_audit(
                s,
                organization_id=account.organization_id,
                user_id=task.requested_by_id,
                user_email=requester(s, task),
                event=AuditEvent.CLOUD_ACCOUNT_VALIDATED,
                resource_type="cloud_account",
                resource_id=account.id,
                resource_name=account.name,
                status=AuditStatus.SUCCESS if result.valid else AuditStatus.FAILURE,
                details={"status": account.status, **({"error": result.error} if result.error else {})},
            )
            return {"status": account.status, "valid": result.valid}

    def preflight(task: TaskContext) -> dict[str, Any]:
        cloud, account_ctx = access(task)
        payload = task.payload
        require_access(cloud, account_ctx)
        if payload.get("network"):
            network = NetworkRef.from_doc(payload["network"])
            verify_network(cloud, account_ctx, network, payload["region"], task.progress)
        machine = cloud.validate_placement(account_ctx, payload["region"], payload["zone"], payload["machine_type"])
        return {"machine": asdict(machine)}

    def plan(task: TaskContext) -> dict[str, Any]:
        cloud, account_ctx = access(task)
        summary = cloud.plan_infrastructure(account_ctx, request_from_dict(task.payload["request"]), task.progress)
        return {
            "add": summary.to_add,
            "change": summary.to_change,
            "destroy": summary.to_destroy,
            "summary": summary.describe(),
        }

    def apply(task: TaskContext) -> dict[str, Any]:
        cloud, account_ctx = access(task)
        state = cloud.apply_infrastructure(account_ctx, request_from_dict(task.payload["request"]), task.progress)
        return {"nodes": [asdict(n) for n in state.nodes], "resources": state.resources, "outputs": state.outputs}

    def destroy(task: TaskContext) -> dict[str, Any]:
        cloud, account_ctx = access(task)
        cloud.destroy_infrastructure(account_ctx, request_from_dict(task.payload["request"]), task.progress)
        return {"destroyed": True}

    # ------------------------------------------------------- monitoring-worker

    def describe(cloud: CloudProvider, account_ctx: CloudAccountContext, lookup: NetworkLookup) -> NetworkDetails:
        try:
            return cloud.describe_network(account_ctx, lookup)
        except PlatformError as err:
            return failed_lookup(lookup, err)

    def describe_network(task: TaskContext) -> dict[str, Any]:
        cloud, account_ctx = access(task)
        lookup = task.payload["lookup"]
        return describe(
            cloud, account_ctx, NetworkLookup(lookup["region"], lookup["vpc"], tuple(lookup["subnets"]))
        ).to_dict()

    def validate_network(task: TaskContext) -> dict[str, Any]:
        with session_scope(sf) as s:
            network = s.get(Network, uuid.UUID(task.payload["network_id"]))
            if network is None or network.organization_id != task.organization_id:
                raise ProvisioningError("The network was removed.", code="NETWORK_MISSING")
            account = account_of(s, task, network.cloud_account_id)
            cloud, account_ctx = registry.cloud(account.provider), build_account_context(account)
            lookup = NetworkLookup(network.region, network.vpc, tuple(network.subnets or []))
        details = describe(cloud, account_ctx, lookup)
        with session_scope(sf) as s:
            network = s.get(Network, uuid.UUID(task.payload["network_id"]))
            if network is None:
                raise ProvisioningError("The network was removed.", code="NETWORK_MISSING")
            now = utcnow()
            if details.valid:
                network.status = NetworkStatus.AVAILABLE.value
                network.last_available_at = now
            elif network.last_available_at is not None:
                network.status = NetworkStatus.UNAVAILABLE.value
            else:
                network.status = NetworkStatus.FAILED.value
            network.details = details.to_dict()
            network.last_validated_at = now
            record_audit(
                s,
                organization_id=network.organization_id,
                user_id=task.requested_by_id,
                user_email=requester(s, task),
                event=AuditEvent.NETWORK_VALIDATED,
                resource_type="network",
                resource_id=network.id,
                resource_name=network.name,
                status=AuditStatus.SUCCESS if details.valid else AuditStatus.FAILURE,
                details={
                    "status": network.status,
                    "zones": details.zones,
                    "warnings": len(details.warnings),
                    **({"error": details.error} if details.error else {}),
                },
            )
            return {"status": network.status}

    def refresh_cluster(task: TaskContext) -> dict[str, Any]:
        with session_scope(sf) as s:
            cluster = s.get(Cluster, uuid.UUID(task.payload["cluster_id"]))
            if cluster is None or cluster.organization_id != task.organization_id:
                raise ProvisioningError("The cluster no longer exists.", code="CLUSTER_NOT_FOUND")
            if cluster.cloud_account_id is None:
                raise ProvisioningError("The cluster's cloud account was removed.", code="CLOUD_ACCOUNT_MISSING")
            account = account_of(s, task, cluster.cloud_account_id)
            nodes = queries.active_nodes(s, cluster.id)
            health.refresh(s, cluster, nodes, build_account_context(account))
            return {"nodes": len(nodes)}

    return {
        "validate_account": validate_account,
        "preflight": preflight,
        "plan": plan,
        "apply": apply,
        "destroy": destroy,
        "describe_network": describe_network,
        "validate_network": validate_network,
        "refresh_cluster": refresh_cluster,
    }


def inline_tasks(platform: Platform) -> CloudTasks:
    """Run every task immediately in this process (tests, and the single-process "all" role)."""
    runner = TaskRunner(platform.session_factory, build_handlers(platform), {TERRAFORM, MONITORING}, "inline")
    return CloudTasks(platform.session_factory, inline=runner)
