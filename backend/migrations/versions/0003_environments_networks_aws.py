"""Environments, registered networks and AWS accounts (docs/adr/0013, docs/adr/0014)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-27 15:00:00

Data changes:

* every organization gets an AWS external ID;
* organizations with clusters get an environment named ``default`` (type PRODUCTION, the cautious
  assumption) holding all their existing clusters, whose desired state records it;
* existing clusters keep ``network_id`` NULL: they run in the dedicated VPC they were created
  with (docs/adr/0006).

Like 0002, columns are added and dropped with plain ALTER TABLE statements (PostgreSQL, and
SQLite 3.35 or later). The one exception is the two new foreign keys on ``clusters`` in SQLite,
which cannot be added by ALTER TABLE: SQLite rebuilds that table in batch mode, which keeps its
partial unique index (a migration test checks it).
"""

import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
DEFAULT_ENVIRONMENT = "default"
CLUSTER_REFERENCES = {"environment_id": "environments", "network_id": "networks"}
DOWNGRADED_AWS = {
    "valid": False,
    "checks": [],
    "missing_permissions": [],
    "error": {
        "code": "AWS_NOT_SUPPORTED",
        "message": "AWS accounts need a newer version of the platform.",
        "suggested_action": "Upgrade the platform again to use this account.",
    },
}

organizations = sa.table("organizations", sa.column("id", sa.Uuid()), sa.column("external_id", sa.String()))
environments = sa.table(
    "environments",
    sa.column("id", sa.Uuid()),
    sa.column("organization_id", sa.Uuid()),
    sa.column("name", sa.String()),
    sa.column("type", sa.String()),
    sa.column("description", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
clusters = sa.table(
    "clusters",
    sa.column("id", sa.Uuid()),
    sa.column("organization_id", sa.Uuid()),
    sa.column("environment_id", sa.Uuid()),
    sa.column("desired_state", JSON),
)
accounts = sa.table(
    "cloud_accounts",
    sa.column("id", sa.Uuid()),
    sa.column("provider", sa.String()),
    sa.column("status", sa.String()),
    sa.column("validation_result", JSON),
)


def _sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _add_references(table: str, references: dict[str, str]) -> None:
    """Nullable UUID columns referencing ``<target>.id`` with ON DELETE SET NULL, and their indexes."""
    if _sqlite():
        with op.batch_alter_table(table, recreate="always") as batch:
            for column, target in references.items():
                batch.add_column(sa.Column(column, sa.Uuid(), nullable=True))
                batch.create_foreign_key(
                    op.f(f"fk_{table}_{column}_{target}"), target, [column], ["id"], ondelete="SET NULL"
                )
    else:
        for column, target in references.items():
            op.add_column(table, sa.Column(column, sa.Uuid(), nullable=True))
            op.create_foreign_key(
                op.f(f"fk_{table}_{column}_{target}"), table, target, [column], ["id"], ondelete="SET NULL"
            )
    for column in references:
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column], unique=False)


def _drop_references(table: str, references: dict[str, str]) -> None:
    for column in references:
        op.drop_index(op.f(f"ix_{table}_{column}"), table_name=table)
    if _sqlite():
        with op.batch_alter_table(table, recreate="always") as batch:
            for column, target in references.items():
                batch.drop_constraint(op.f(f"fk_{table}_{column}_{target}"), type_="foreignkey")
                batch.drop_column(column)
        return
    for column, target in references.items():
        op.drop_constraint(op.f(f"fk_{table}_{column}_{target}"), table, type_="foreignkey")
        op.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


def _rows(query: Any) -> list[Any]:
    return list(op.get_bind().execute(query).all())


def upgrade() -> None:
    op.create_table(
        "environments",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_environments_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_environments_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_environments")),
        sa.UniqueConstraint("organization_id", "name", name=op.f("uq_environments_organization_id_name")),
    )
    op.create_index(op.f("ix_environments_organization_id"), "environments", ["organization_id"], unique=False)

    op.create_table(
        "networks",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("cloud_account_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("region", sa.String(length=50), nullable=False),
        sa.Column("vpc", sa.String(length=255), nullable=False),
        sa.Column("subnets", JSON, nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("details", JSON, nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["cloud_account_id"], ["cloud_accounts.id"], name=op.f("fk_networks_cloud_account_id_cloud_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_networks_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environments.id"],
            name=op.f("fk_networks_environment_id_environments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_networks_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_networks")),
        sa.UniqueConstraint("environment_id", "name", name=op.f("uq_networks_environment_id_name")),
    )
    for column in ("organization_id", "environment_id", "cloud_account_id"):
        op.create_index(op.f(f"ix_networks_{column}"), "networks", [column], unique=False)

    # Every organization gets its AWS external ID (docs/adr/0014).
    op.add_column(
        "organizations", sa.Column("external_id", sa.String(length=64), nullable=False, server_default="")
    )
    for (org_id,) in _rows(sa.select(organizations.c.id)):
        op.execute(
            organizations.update()
            .where(organizations.c.id == org_id)
            .values(external_id=f"byoc-{secrets.token_hex(16)}")
        )
    op.create_index("uq_organizations_external_id", "organizations", ["external_id"], unique=True)
    if not _sqlite():
        op.alter_column("organizations", "external_id", server_default=None)

    op.add_column("cloud_accounts", sa.Column("role_arn", sa.String(length=2048), nullable=True))
    _add_references("clusters", CLUSTER_REFERENCES)

    # Existing clusters move into a "default" environment of their organization.
    now = datetime.now(UTC)
    for (org_id,) in _rows(sa.select(clusters.c.organization_id).distinct()):
        environment_id = uuid.uuid4()
        op.execute(
            environments.insert().values(
                id=environment_id,
                organization_id=org_id,
                name=DEFAULT_ENVIRONMENT,
                type="PRODUCTION",
                description="Clusters created before environments existed.",
                created_at=now,
                updated_at=now,
            )
        )
        reference = {"id": str(environment_id), "name": DEFAULT_ENVIRONMENT, "type": "PRODUCTION"}
        for cluster_id, desired in _rows(
            sa.select(clusters.c.id, clusters.c.desired_state).where(clusters.c.organization_id == org_id)
        ):
            op.execute(
                clusters.update()
                .where(clusters.c.id == cluster_id)
                .values(environment_id=environment_id, desired_state={**(desired or {}), "environment": reference})
            )


def downgrade() -> None:
    placed = sa.table("clusters", sa.column("network_id", sa.Uuid()), sa.column("deleted_at", sa.DateTime()))
    if _rows(sa.select(placed.c.network_id).where(placed.c.network_id.is_not(None), placed.c.deleted_at.is_(None))):
        # 0002 would treat them as clusters with a dedicated VPC and plan network changes.
        raise RuntimeError("Delete the clusters that run in registered networks before downgrading below 0003.")
    # 0002 knows GCP only: AWS accounts stay, marked FAILED with an explanation.
    op.execute(
        accounts.update()
        .where(accounts.c.provider == "aws")
        .values(status="FAILED", validation_result=DOWNGRADED_AWS)
    )
    for cluster_id, desired in _rows(sa.select(clusters.c.id, clusters.c.desired_state)):
        if isinstance(desired, dict) and ("environment" in desired or "network" in desired):
            kept = {k: v for k, v in desired.items() if k not in ("environment", "network")}
            op.execute(clusters.update().where(clusters.c.id == cluster_id).values(desired_state=kept))
    _drop_references("clusters", CLUSTER_REFERENCES)
    op.execute("ALTER TABLE cloud_accounts DROP COLUMN role_arn")
    op.drop_index("uq_organizations_external_id", table_name="organizations")
    op.execute("ALTER TABLE organizations DROP COLUMN external_id")
    for column in ("cloud_account_id", "environment_id", "organization_id"):
        op.drop_index(op.f(f"ix_networks_{column}"), table_name="networks")
    op.drop_table("networks")
    op.drop_index(op.f("ix_environments_organization_id"), table_name="environments")
    op.drop_table("environments")
