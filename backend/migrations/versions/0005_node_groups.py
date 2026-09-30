"""Node groups of the dedicated layout (docs/adr/0016)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-30 12:00:00

Existing nodes belong to the combined layout: both columns stay NULL.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cluster_nodes", sa.Column("node_group", sa.String(length=20), nullable=True))
    op.add_column("cluster_nodes", sa.Column("machine_type", sa.String(length=60), nullable=True))


def downgrade() -> None:
    op.execute("ALTER TABLE cluster_nodes DROP COLUMN machine_type")
    op.execute("ALTER TABLE cluster_nodes DROP COLUMN node_group")
