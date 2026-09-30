"""v2 domain model: separate lifecycle and health, Operator role, keyless cloud accounts,
TRD audit events, delete pre-emption indexes (docs/migration-plan.md section 7)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27 12:00:00

Column renames and drops use plain ALTER TABLE statements (PostgreSQL, and SQLite 3.35 or
later) instead of SQLite batch mode, which would rebuild tables and their partial indexes.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
ACTIVE = "status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')"
V1_ACTIVE_MUTATION = f"{ACTIVE} AND operation_type <> 'HEALTH_CHECK'"
V2_ACTIVE_MUTATION = f"{ACTIVE} AND operation_type NOT IN ('HEALTH_CHECK', 'DELETE_CLUSTER')"
V2_ACTIVE_DELETE = f"{ACTIVE} AND operation_type = 'DELETE_CLUSTER'"
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")

CLUSTER_LIFECYCLE = {
    "PROVISIONING": "CREATING",
    "RUNNING": "ACTIVE",
    "SCALING": "SCALING",
    "DELETING": "DELETING",
    "DELETED": "DELETED",
    "FAILED": "FAILED",
}
CLUSTER_LIFECYCLE_DOWN = {"CREATING": "PROVISIONING", "ACTIVE": "RUNNING", "UPGRADING": "RUNNING"}
CLUSTER_HEALTH = {"HEALTHY": "HEALTHY", "WARNING": "DEGRADED", "CRITICAL": "UNHEALTHY", "UNKNOWN": "UNKNOWN"}
CLUSTER_HEALTH_DOWN = {"DEGRADED": "WARNING", "UNHEALTHY": "CRITICAL"}
NODE_LIFECYCLE = {
    "PROVISIONING": "BOOTSTRAPPING",
    "BOOTSTRAPPING": "BOOTSTRAPPING",
    "RUNNING": "ACTIVE",
    "UNREACHABLE": "ACTIVE",
    "STOPPED": "ACTIVE",
    "MISSING": "ACTIVE",
    "DECOMMISSIONING": "ACTIVE",
    "DELETED": "DELETED",
}
NODE_LIFECYCLE_DOWN = {"ACTIVE": "RUNNING"}
NODE_HEALTH = {"HEALTHY": "HEALTHY", "WARNING": "UNKNOWN", "CRITICAL": "UNHEALTHY", "UNKNOWN": "UNKNOWN"}
NODE_HEALTH_DOWN = {"UNHEALTHY": "CRITICAL"}
ENGINE_VERSIONS = {"9.5": "9.5.4", "9.4": "9.4.7", "8.19": "8.19.22"}
ACCOUNT_STATUS = {"PENDING_VALIDATION": "PENDING", "VALID": "CONNECTED", "INVALID": "FAILED"}
ACCOUNT_STATUS_DOWN = {
    "PENDING": "PENDING_VALIDATION",
    "VALIDATING": "PENDING_VALIDATION",
    "CONNECTED": "VALID",
    "FAILED": "INVALID",
    "DISCONNECTED": "INVALID",
}
ROLE = {"DEVELOPER": "OPERATOR"}
ROLE_DOWN = {"OPERATOR": "DEVELOPER"}

AUDIT_RENAMES = {
    "SIGNUP": "USER_SIGNED_UP",
    "SWITCH_ORGANIZATION": "ORGANIZATION_SWITCHED",
    "CREATE_CLOUD_ACCOUNT": "CLOUD_ACCOUNT_CREATED",
    "VALIDATE_CLOUD_ACCOUNT": "CLOUD_ACCOUNT_VALIDATED",
    "DELETE_CLOUD_ACCOUNT": "CLOUD_ACCOUNT_DELETED",
    "CANCEL_OPERATION": "OPERATION_CANCEL_REQUESTED",
    "RETRY_OPERATION": "OPERATION_RETRIED",
    "ADD_MEMBER": "MEMBER_ADDED",
    "UPDATE_MEMBER_ROLE": "MEMBER_ROLE_CHANGED",
    "REMOVE_MEMBER": "MEMBER_REMOVED",
    "SIMULATE_FAULT": "FAULT_INJECTED",
}
AUDIT_OPERATIONS = {
    "CREATE_CLUSTER": ("CLUSTER_CREATE_STARTED", "CLUSTER_CREATED"),
    "SCALE_CLUSTER": ("CLUSTER_SCALE_STARTED", "CLUSTER_SCALE_COMPLETED"),
    "DELETE_CLUSTER": ("CLUSTER_DELETE_STARTED", "CLUSTER_DELETE_COMPLETED"),
    "HEALTH_CHECK": ("CLUSTER_HEALTH_CHECK_REQUESTED", "CLUSTER_HEALTH_CHECK_COMPLETED"),
}

KEY_AUTH_REMOVED = {
    "valid": False,
    "checks": [],
    "missing_permissions": [],
    "error": {
        "code": "KEY_AUTH_REMOVED",
        "message": "Service-account keys are no longer supported.",
        "reason": "The stored key was deleted when the platform moved to keyless access.",
        "suggested_action": (
            "Allow the platform to impersonate this service account instead of using its key "
            "(docs/gcp-setup.md), then validate the account again."
        ),
    },
}

clusters = sa.table(
    "clusters",
    sa.column("id", sa.Uuid()),
    sa.column("lifecycle_state", sa.String()),
    sa.column("health", sa.String()),
    sa.column("engine_version", sa.String()),
    sa.column("desired_state", JSON),
    sa.column("health_details", JSON),
    sa.column("status_message", sa.Text()),
)
nodes = sa.table(
    "cluster_nodes",
    sa.column("id", sa.Uuid()),
    sa.column("lifecycle_state", sa.String()),
    sa.column("health", sa.String()),
)
accounts = sa.table(
    "cloud_accounts",
    sa.column("id", sa.Uuid()),
    sa.column("status", sa.String()),
    sa.column("auth_type", sa.String()),
    sa.column("validation_result", JSON),
    sa.column("last_validated_at", sa.DateTime(timezone=True)),
    sa.column("last_connected_at", sa.DateTime(timezone=True)),
)
members = sa.table("organization_members", sa.column("id", sa.Uuid()), sa.column("role", sa.String()))
audit = sa.table(
    "audit_logs",
    sa.column("id", sa.Uuid()),
    sa.column("action", sa.String()),
    sa.column("status", sa.String()),
    sa.column("details", JSON),
)
operations = sa.table(
    "operations",
    sa.column("id", sa.Uuid()),
    sa.column("cluster_id", sa.Uuid()),
    sa.column("operation_type", sa.String()),
    sa.column("status", sa.String()),
    sa.column("metadata", JSON),
    sa.column("error_code", sa.String()),
    sa.column("error_message", sa.Text()),
    sa.column("completed_at", sa.DateTime(timezone=True)),
)


def _map_column(table: sa.TableClause, column: str, mapping: dict[str, str]) -> None:
    for old, new in mapping.items():
        if old != new:
            op.execute(table.update().where(table.c[column] == old).values({column: new}))


def _each(table: sa.TableClause, *columns: str) -> list[Any]:
    return list(op.get_bind().execute(sa.select(*(table.c[c] for c in columns))).all())


def _rename_column(table: str, old: str, new: str) -> None:
    op.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")


def _drop_column(table: str, column: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


def _health_details(details: Any, cluster_map: dict[str, str], node_map: dict[str, str]) -> Any:
    if not isinstance(details, dict) or "state" not in details:
        return details
    updated = dict(details)
    updated["state"] = cluster_map.get(details["state"], details["state"])
    updated["nodes"] = [
        {**n, "state": node_map.get(n.get("state"), n.get("state"))} if isinstance(n, dict) else n
        for n in details.get("nodes") or []
    ]
    return updated


def _audit_v2(action: str, status: str, details: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if action == "LOGIN":
        return ("LOGIN_SUCCEEDED", "SUCCESS", details) if status == "SUCCESS" else ("LOGIN_FAILED", "FAILURE", details)
    if action in AUDIT_OPERATIONS:
        started, completed = AUDIT_OPERATIONS[action]
        if status == "ACCEPTED":
            return started, "SUCCESS", details
        if status == "SUCCESS":
            return completed, "SUCCESS", details
        details = {**details, "operation_type": action}
        if details.get("operation_status") == "CANCELLED":
            return "OPERATION_CANCELLED", "SUCCESS", details
        return "OPERATION_FAILED", "FAILURE", details
    if action in AUDIT_RENAMES:
        for key in ("role", "from", "to"):
            if details.get(key) in ROLE:
                details = {**details, key: ROLE[details[key]]}
        return AUDIT_RENAMES[action], "SUCCESS" if status in ("SUCCESS", "ACCEPTED") else status, details
    return action, status, details


def _audit_v1(action: str, status: str, details: dict[str, Any]) -> tuple[str, str]:
    if action == "LOGIN_SUCCEEDED":
        return "LOGIN", "SUCCESS"
    if action == "LOGIN_FAILED":
        return "LOGIN", "FAILURE"
    for verb, (started, completed) in AUDIT_OPERATIONS.items():
        if action == started:
            return verb, "ACCEPTED"
        if action == completed:
            return verb, "SUCCESS"
    if action in ("OPERATION_FAILED", "OPERATION_CANCELLED"):
        return str(details.get("operation_type") or "UNKNOWN_OPERATION"), "FAILURE"
    for old, new in AUDIT_RENAMES.items():
        if action == new:
            return old, status
    return action, status


def _update_json(table: sa.TableClause, column: str, change: Callable[[Any], Any]) -> None:
    for row_id, value in _each(table, "id", column):
        updated = change(value)
        if updated != value:
            op.execute(table.update().where(table.c.id == row_id).values({column: updated}))


def upgrade() -> None:
    # ------------------------------------------------------------------ schema
    op.drop_index("ix_clusters_status", table_name="clusters")
    _rename_column("clusters", "status", "lifecycle_state")
    op.create_index(op.f("ix_clusters_lifecycle_state"), "clusters", ["lifecycle_state"], unique=False)

    _rename_column("cluster_nodes", "status", "lifecycle_state")
    op.add_column("cluster_nodes", sa.Column("health_warnings", JSON, nullable=False, server_default=sa.text("'[]'")))
    op.add_column(
        "cluster_nodes",
        sa.Column("agent_status", sa.String(length=20), nullable=False, server_default="NOT_REPORTED"),
    )

    op.add_column("cloud_accounts", sa.Column("region", sa.String(length=50), nullable=True))
    op.add_column("cloud_accounts", sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True))

    op.drop_index(
        "uq_operations_active_mutation",
        table_name="operations",
        postgresql_where=sa.text(V1_ACTIVE_MUTATION),
        sqlite_where=sa.text(V1_ACTIVE_MUTATION),
    )
    op.create_index(
        "uq_operations_active_mutation",
        "operations",
        ["cluster_id"],
        unique=True,
        postgresql_where=sa.text(V2_ACTIVE_MUTATION),
        sqlite_where=sa.text(V2_ACTIVE_MUTATION),
    )
    op.create_index(
        "uq_operations_active_delete",
        "operations",
        ["cluster_id"],
        unique=True,
        postgresql_where=sa.text(V2_ACTIVE_DELETE),
        sqlite_where=sa.text(V2_ACTIVE_DELETE),
    )

    # -------------------------------------------------------------------- data
    _map_column(clusters, "lifecycle_state", CLUSTER_LIFECYCLE)
    _map_column(clusters, "health", CLUSTER_HEALTH)
    _map_column(clusters, "engine_version", ENGINE_VERSIONS)
    _update_json(
        clusters,
        "desired_state",
        lambda doc: (
            {**doc, "engine": {**doc["engine"], "version": ENGINE_VERSIONS.get(doc["engine"].get("version"))}}
            if isinstance(doc, dict)
            and isinstance(doc.get("engine"), dict)
            and doc["engine"].get("version") in ENGINE_VERSIONS
            else doc
        ),
    )
    _update_json(clusters, "health_details", lambda d: _health_details(d, CLUSTER_HEALTH, NODE_HEALTH))
    _map_column(nodes, "lifecycle_state", NODE_LIFECYCLE)
    _map_column(nodes, "health", NODE_HEALTH)
    _map_column(members, "role", ROLE)

    for account_id, status, auth_type, validated_at in _each(
        accounts, "id", "status", "auth_type", "last_validated_at"
    ):
        values: dict[str, Any] = {"status": ACCOUNT_STATUS.get(status, status)}
        if status == "VALID":
            values["last_connected_at"] = validated_at
        if auth_type == "service_account_key":
            values.update(
                auth_type="impersonation",
                status="DISCONNECTED" if status == "VALID" else "FAILED",
                validation_result=KEY_AUTH_REMOVED,
            )
        op.execute(accounts.update().where(accounts.c.id == account_id).values(values))
    for column in ("encrypted_credentials", "credentials_fingerprint", "state_bucket"):
        _drop_column("cloud_accounts", column)

    for entry_id, action, status, details in _each(audit, "id", "action", "status", "details"):
        new_action, new_status, new_details = _audit_v2(action, status, dict(details or {}))
        if (new_action, new_status, new_details) != (action, status, details or {}):
            op.execute(
                audit.update()
                .where(audit.c.id == entry_id)
                .values(action=new_action, status=new_status, details=new_details)
            )

    now = datetime.now(UTC)
    for op_id, cluster_id, kind, status, metadata in _each(
        operations, "id", "cluster_id", "operation_type", "status", "metadata"
    ):
        metadata = dict(metadata or {})
        params = dict(metadata.get("params") or {})
        changed = False
        if "previous_status" in params:
            previous = params.pop("previous_status")
            params["previous_lifecycle"] = CLUSTER_LIFECYCLE.get(previous, previous)
            changed = True
        values: dict[str, Any] = {}
        scale_down = kind == "SCALE_CLUSTER" and int(params.get("to") or 0) < int(params.get("from") or 0)
        if scale_down and status not in TERMINAL:
            error = {
                "code": "SCALE_DOWN_NOT_SUPPORTED",
                "message": "Scaling down is not supported; the operation was stopped by the upgrade.",
            }
            metadata["error"] = error
            values.update(
                status="FAILED", error_code=error["code"], error_message=error["message"], completed_at=now
            )
            op.execute(
                clusters.update()
                .where(clusters.c.id == cluster_id, clusters.c.lifecycle_state == "SCALING")
                .values(lifecycle_state="ACTIVE", status_message=error["message"])
            )
            changed = True
        if changed:
            metadata["params"] = params
            op.execute(operations.update().where(operations.c.id == op_id).values(metadata=metadata, **values))


def downgrade() -> None:
    for op_id, metadata in _each(operations, "id", "metadata"):
        metadata = dict(metadata or {})
        params = dict(metadata.get("params") or {})
        if "previous_lifecycle" in params:
            previous = params.pop("previous_lifecycle")
            params["previous_status"] = CLUSTER_LIFECYCLE_DOWN.get(previous, previous)
            metadata["params"] = params
            op.execute(operations.update().where(operations.c.id == op_id).values(metadata=metadata))

    for entry_id, action, status, details in _each(audit, "id", "action", "status", "details"):
        old_action, old_status = _audit_v1(action, status, dict(details or {}))
        if (old_action, old_status) != (action, status):
            op.execute(audit.update().where(audit.c.id == entry_id).values(action=old_action, status=old_status))

    op.add_column("cloud_accounts", sa.Column("state_bucket", sa.String(length=222), nullable=True))
    op.add_column("cloud_accounts", sa.Column("credentials_fingerprint", sa.String(length=100), nullable=True))
    op.add_column("cloud_accounts", sa.Column("encrypted_credentials", sa.Text(), nullable=True))
    _map_column(accounts, "status", ACCOUNT_STATUS_DOWN)
    _map_column(members, "role", ROLE_DOWN)
    _map_column(nodes, "health", NODE_HEALTH_DOWN)
    _map_column(nodes, "lifecycle_state", NODE_LIFECYCLE_DOWN)
    _update_json(clusters, "health_details", lambda d: _health_details(d, CLUSTER_HEALTH_DOWN, NODE_HEALTH_DOWN))
    _map_column(clusters, "health", CLUSTER_HEALTH_DOWN)
    _map_column(clusters, "lifecycle_state", CLUSTER_LIFECYCLE_DOWN)

    op.drop_index(
        "uq_operations_active_delete",
        table_name="operations",
        postgresql_where=sa.text(V2_ACTIVE_DELETE),
        sqlite_where=sa.text(V2_ACTIVE_DELETE),
    )
    op.drop_index(
        "uq_operations_active_mutation",
        table_name="operations",
        postgresql_where=sa.text(V2_ACTIVE_MUTATION),
        sqlite_where=sa.text(V2_ACTIVE_MUTATION),
    )
    op.create_index(
        "uq_operations_active_mutation",
        "operations",
        ["cluster_id"],
        unique=True,
        postgresql_where=sa.text(V1_ACTIVE_MUTATION),
        sqlite_where=sa.text(V1_ACTIVE_MUTATION),
    )
    _drop_column("cloud_accounts", "last_connected_at")
    _drop_column("cloud_accounts", "region")
    _drop_column("cluster_nodes", "agent_status")
    _drop_column("cluster_nodes", "health_warnings")
    _rename_column("cluster_nodes", "lifecycle_state", "status")
    op.drop_index(op.f("ix_clusters_lifecycle_state"), table_name="clusters")
    _rename_column("clusters", "lifecycle_state", "status")
    op.create_index(op.f("ix_clusters_status"), "clusters", ["status"], unique=False)
