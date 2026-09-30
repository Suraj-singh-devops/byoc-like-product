from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.models import Base

BACKEND = Path(__file__).resolve().parents[2]


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def table_names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_migrations_match_models_and_downgrade(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = alembic_config(url)
    command.upgrade(config, "head")
    assert set(Base.metadata.tables) <= table_names(url)
    # Raises if the models have changes that no migration covers.
    command.check(config)
    command.downgrade(config, "base")
    assert table_names(url) <= {"alembic_version"}


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _type(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return sa.JSON()
    if isinstance(value, uuid.UUID):
        return sa.Uuid()
    if isinstance(value, datetime):
        return sa.DateTime(timezone=True)
    if isinstance(value, bool):
        return sa.Boolean()
    if isinstance(value, int):
        return sa.Integer()
    return sa.String()


def insert(conn: sa.Connection, name: str, *records: dict[str, Any]) -> None:
    """Insert rows into a table of the v1 schema (the ORM models describe v2)."""
    stamped = [{"created_at": NOW, **r} for r in records]
    if name != "audit_logs":
        stamped = [{"updated_at": NOW, **r} for r in stamped]
    columns = [sa.column(k, _type(v)) for k, v in stamped[0].items()]
    conn.execute(sa.table(name, *columns).insert(), stamped)


def seed_v1(engine: sa.Engine) -> None:
    org, user, account, key_account, cluster = (uuid.uuid4() for _ in range(5))
    with engine.begin() as c:
        insert(c, "organizations", {"id": org, "name": "Acme", "slug": "acme"})
        insert(
            c,
            "users",
            {"id": user, "email": "dev@acme.example", "name": "Dev", "password_hash": "x", "is_active": True},
        )
        insert(
            c,
            "organization_members",
            {"id": uuid.uuid4(), "organization_id": org, "user_id": user, "role": "DEVELOPER"},
        )
        common = {"organization_id": org, "provider": "gcp", "status": "VALID", "last_validated_at": NOW}
        insert(
            c,
            "cloud_accounts",
            {
                **common,
                "id": account,
                "name": "keyless",
                "project_id": "acme-prod",
                "auth_type": "impersonation",
                "service_account_email": "sa@acme-prod.iam.gserviceaccount.com",
                "encrypted_credentials": "",
            },
            {
                **common,
                "id": key_account,
                "name": "with-key",
                "project_id": "acme-dev",
                "auth_type": "service_account_key",
                "service_account_email": "cp@acme-dev.iam.gserviceaccount.com",
                "encrypted_credentials": "gAAAA-ciphertext",
            },
        )
        insert(
            c,
            "clusters",
            {
                "id": cluster,
                "organization_id": org,
                "name": "search",
                "engine": "elasticsearch",
                "engine_version": "9.5",
                "cloud_provider": "gcp",
                "cloud_account_id": account,
                "project_id": "acme-prod",
                "region": "asia-south1",
                "zone": "asia-south1-a",
                "machine_type": "e2-standard-8",
                "node_count": 3,
                "storage_gb": 100,
                "storage_type": "pd-balanced",
                "high_availability": True,
                "status": "SCALING",
                "health": "WARNING",
                "health_details": {"state": "WARNING", "nodes": [{"name": "node-1", "state": "CRITICAL"}]},
                "metrics_summary": {},
                "desired_state": {"engine": {"type": "elasticsearch", "version": "9.5"}, "nodes": {"count": 3}},
                "actual_state": {},
                "generation": 2,
                "observed_generation": 1,
                "resource_prefix": "search-1a2b",
            },
        )
        insert(
            c,
            "cluster_nodes",
            {
                "id": uuid.uuid4(),
                "cluster_id": cluster,
                "name": "node-1",
                "ordinal": 1,
                "zone": "asia-south1-a",
                "role": "master,data,ingest",
                "status": "UNREACHABLE",
                "health": "WARNING",
                "health_reasons": ["Agent unavailable"],
            },
        )
        insert(
            c,
            "operations",
            {
                "id": uuid.uuid4(),
                "organization_id": org,
                "cluster_id": cluster,
                "operation_type": "SCALE_CLUSTER",
                "status": "PROVISIONING",
                "progress": 30,
                "cancel_requested": False,
                "attempt": 1,
                "metadata": {"params": {"from": 5, "to": 3, "previous_status": "RUNNING"}},
            },
        )
        insert(
            c,
            "audit_logs",
            *(
                {
                    "id": uuid.uuid4(),
                    "organization_id": org,
                    "action": a,
                    "resource_type": "cluster",
                    "status": st,
                    "details": d,
                }
                for a, st, d in (
                    ("LOGIN", "FAILURE", {}),
                    ("SCALE_CLUSTER", "ACCEPTED", {}),
                    ("CREATE_CLUSTER", "FAILURE", {"operation_status": "CANCELLED"}),
                    ("DELETE_CLUSTER", "FAILURE", {"operation_status": "FAILED"}),
                    ("UPDATE_MEMBER_ROLE", "SUCCESS", {"from": "VIEWER", "to": "DEVELOPER"}),
                )
            ),
        )


def rows(engine: sa.Engine, sql: str) -> list[dict[str, Any]]:
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(sa.text(sql))]


def test_v1_data_is_migrated_to_v2_and_back(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'data.db'}"
    config = alembic_config(url)
    command.upgrade(config, "0001")
    engine = create_engine(url)
    try:
        seed_v1(engine)
        command.upgrade(config, "head")

        (cluster,) = rows(
            engine,
            "SELECT lifecycle_state, health, engine_version, desired_state, health_details, "
            "status_message FROM clusters",
        )
        assert (cluster["lifecycle_state"], cluster["health"], cluster["engine_version"]) == (
            "ACTIVE",
            "DEGRADED",
            "9.5.4",
        ), "the running scale-down was stopped and the cluster returned to ACTIVE"
        assert json.loads(cluster["desired_state"])["engine"]["version"] == "9.5.4"
        details = json.loads(cluster["health_details"])
        assert details["state"] == "DEGRADED" and details["nodes"][0]["state"] == "UNHEALTHY"
        (node,) = rows(engine, "SELECT lifecycle_state, health, agent_status, health_warnings FROM cluster_nodes")
        assert (node["lifecycle_state"], node["health"], node["agent_status"]) == ("ACTIVE", "UNKNOWN", "NOT_REPORTED")
        assert json.loads(node["health_warnings"]) == []
        assert rows(engine, "SELECT role FROM organization_members") == [{"role": "OPERATOR"}]

        accounts = {r["name"]: r for r in rows(engine, "SELECT * FROM cloud_accounts")}
        assert "encrypted_credentials" not in accounts["keyless"] and "state_bucket" not in accounts["keyless"]
        assert accounts["keyless"]["status"] == "CONNECTED" and accounts["keyless"]["last_connected_at"]
        assert (accounts["with-key"]["status"], accounts["with-key"]["auth_type"]) == ("DISCONNECTED", "impersonation")
        assert json.loads(accounts["with-key"]["validation_result"])["error"]["code"] == "KEY_AUTH_REMOVED"

        (op,) = rows(engine, "SELECT status, error_code, metadata FROM operations")
        assert (op["status"], op["error_code"]) == ("FAILED", "SCALE_DOWN_NOT_SUPPORTED")
        assert json.loads(op["metadata"])["params"] == {"from": 5, "to": 3, "previous_lifecycle": "ACTIVE"}

        audit = {r["action"]: r for r in rows(engine, "SELECT action, status, details FROM audit_logs")}
        assert set(audit) == {
            "LOGIN_FAILED",
            "CLUSTER_SCALE_STARTED",
            "OPERATION_CANCELLED",
            "OPERATION_FAILED",
            "MEMBER_ROLE_CHANGED",
        }
        assert audit["CLUSTER_SCALE_STARTED"]["status"] == "SUCCESS"
        assert json.loads(audit["OPERATION_FAILED"]["details"])["operation_type"] == "DELETE_CLUSTER"
        assert json.loads(audit["MEMBER_ROLE_CHANGED"]["details"])["to"] == "OPERATOR"

        command.downgrade(config, "0001")
        (cluster,) = rows(engine, "SELECT status, health FROM clusters")
        assert (cluster["status"], cluster["health"]) == ("RUNNING", "WARNING")
        assert rows(engine, "SELECT role FROM organization_members") == [{"role": "DEVELOPER"}]
        assert {r["status"] for r in rows(engine, "SELECT status FROM cloud_accounts")} == {"VALID", "INVALID"}
        assert {r["action"] for r in rows(engine, "SELECT action FROM audit_logs")} == {
            "LOGIN",
            "SCALE_CLUSTER",
            "CREATE_CLUSTER",
            "DELETE_CLUSTER",
            "UPDATE_MEMBER_ROLE",
        }
    finally:
        engine.dispose()


def test_existing_clusters_move_into_a_default_environment(tmp_path: Path) -> None:
    """Revision 0003 (docs/adr/0013, docs/adr/0014) on data that 0002 produced."""
    url = f"sqlite:///{tmp_path / 'p1.db'}"
    config = alembic_config(url)
    command.upgrade(config, "0001")
    engine = create_engine(url)
    try:
        seed_v1(engine)
        with engine.begin() as c:
            insert(c, "organizations", {"id": uuid.uuid4(), "name": "Empty", "slug": "empty"})
        command.upgrade(config, "0002")
        command.upgrade(config, "head")

        orgs = rows(engine, "SELECT slug, external_id FROM organizations ORDER BY slug")
        assert [o["slug"] for o in orgs] == ["acme", "empty"]
        assert all(o["external_id"].startswith("byoc-") and len(o["external_id"]) == 37 for o in orgs)
        assert orgs[0]["external_id"] != orgs[1]["external_id"]

        (environment,) = rows(engine, "SELECT id, name, type FROM environments")
        assert (environment["name"], environment["type"]) == ("default", "PRODUCTION")
        (cluster,) = rows(engine, "SELECT environment_id, network_id, desired_state FROM clusters")
        assert uuid.UUID(cluster["environment_id"]) == uuid.UUID(environment["id"])
        assert cluster["network_id"] is None, "older clusters keep their dedicated VPC"
        assert json.loads(cluster["desired_state"])["environment"]["name"] == "default"
        # SQLite rebuilt the clusters table for its new foreign keys; the partial unique index survives.
        (index,) = rows(engine, "SELECT sql FROM sqlite_master WHERE name = 'uq_clusters_org_name_active'")
        assert "WHERE deleted_at IS NULL" in index["sql"]

        command.downgrade(config, "0002")
        assert "environments" not in table_names(url) and "networks" not in table_names(url)
        (cluster,) = rows(engine, "SELECT * FROM clusters")
        assert "environment_id" not in cluster and "environment" not in json.loads(cluster["desired_state"])
        (index,) = rows(engine, "SELECT sql FROM sqlite_master WHERE name = 'uq_clusters_org_name_active'")
        assert "WHERE deleted_at IS NULL" in index["sql"]
    finally:
        engine.dispose()
