"""Cloud tasks: work handed to the terraform-runner and the monitoring-worker (docs/adr/0004)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "cloud_tasks",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("payload", JSON, nullable=False),
        sa.Column("result", JSON, nullable=True),
        sa.Column("error", JSON, nullable=True),
        sa.Column("progress", JSON, nullable=False),
        sa.Column("worker_id", sa.String(length=100), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_cloud_tasks_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"], ["users.id"], name=op.f("fk_cloud_tasks_requested_by_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cloud_tasks")),
    )
    op.create_index(op.f("ix_cloud_tasks_organization_id"), "cloud_tasks", ["organization_id"], unique=False)
    op.create_index(op.f("ix_cloud_tasks_operation_id"), "cloud_tasks", ["operation_id"], unique=False)
    op.create_index("ix_cloud_tasks_role_status_created", "cloud_tasks", ["role", "status", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_cloud_tasks_role_status_created", table_name="cloud_tasks")
    op.drop_index(op.f("ix_cloud_tasks_operation_id"), table_name="cloud_tasks")
    op.drop_index(op.f("ix_cloud_tasks_organization_id"), table_name="cloud_tasks")
    op.drop_table("cloud_tasks")
