"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-09-25 21:51:30.589624
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    
    op.create_table('mock_instances',
    sa.Column('cluster_id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.String(length=100), nullable=False),
    sa.Column('zone', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=63), nullable=False),
    sa.Column('node_name', sa.String(length=63), nullable=False),
    sa.Column('private_ip', sa.String(length=45), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('engine_version', sa.String(length=40), nullable=False),
    sa.Column('boot_started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('faults', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('labels', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mock_instances')),
    sa.UniqueConstraint('project_id', 'zone', 'name', name=op.f('uq_mock_instances_project_id_zone_name'))
    )
    op.create_index(op.f('ix_mock_instances_cluster_id'), 'mock_instances', ['cluster_id'], unique=False)
    op.create_table('organizations',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organizations')),
    sa.UniqueConstraint('slug', name=op.f('uq_organizations_slug'))
    )
    op.create_table('users',
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('audit_logs',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('user_email', sa.String(length=320), nullable=True),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('resource_type', sa.String(length=40), nullable=False),
    sa.Column('resource_id', sa.String(length=64), nullable=True),
    sa.Column('resource_name', sa.String(length=200), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('operation_id', sa.Uuid(), nullable=True),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_audit_logs_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_audit_logs_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index('ix_audit_logs_org_created', 'audit_logs', ['organization_id', 'created_at'], unique=False)
    op.create_table('cloud_accounts',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('project_id', sa.String(length=100), nullable=False),
    sa.Column('auth_type', sa.String(length=40), nullable=False),
    sa.Column('service_account_email', sa.String(length=320), nullable=True),
    sa.Column('encrypted_credentials', sa.Text(), nullable=True),
    sa.Column('credentials_fingerprint', sa.String(length=100), nullable=True),
    sa.Column('state_bucket', sa.String(length=222), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('validation_result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('last_validated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_cloud_accounts_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_cloud_accounts_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cloud_accounts')),
    sa.UniqueConstraint('organization_id', 'name', name=op.f('uq_cloud_accounts_organization_id_name'))
    )
    op.create_index(op.f('ix_cloud_accounts_organization_id'), 'cloud_accounts', ['organization_id'], unique=False)
    op.create_table('organization_members',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_organization_members_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_organization_members_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organization_members')),
    sa.UniqueConstraint('organization_id', 'user_id', name=op.f('uq_organization_members_organization_id_user_id'))
    )
    op.create_index(op.f('ix_organization_members_organization_id'), 'organization_members', ['organization_id'], unique=False)
    op.create_index(op.f('ix_organization_members_user_id'), 'organization_members', ['user_id'], unique=False)
    op.create_table('clusters',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=63), nullable=False),
    sa.Column('engine', sa.String(length=40), nullable=False),
    sa.Column('engine_version', sa.String(length=20), nullable=False),
    sa.Column('cloud_provider', sa.String(length=20), nullable=False),
    sa.Column('cloud_account_id', sa.Uuid(), nullable=True),
    sa.Column('project_id', sa.String(length=100), nullable=False),
    sa.Column('region', sa.String(length=50), nullable=False),
    sa.Column('zone', sa.String(length=50), nullable=False),
    sa.Column('machine_type', sa.String(length=60), nullable=False),
    sa.Column('node_count', sa.Integer(), nullable=False),
    sa.Column('storage_gb', sa.Integer(), nullable=False),
    sa.Column('storage_type', sa.String(length=40), nullable=False),
    sa.Column('high_availability', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('health', sa.String(length=20), nullable=False),
    sa.Column('health_details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('metrics_summary', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('desired_state', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('actual_state', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('observed_generation', sa.Integer(), nullable=False),
    sa.Column('resource_prefix', sa.String(length=50), nullable=False),
    sa.Column('status_message', sa.Text(), nullable=True),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('last_health_check_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['cloud_account_id'], ['cloud_accounts.id'], name=op.f('fk_clusters_cloud_account_id_cloud_accounts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_clusters_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_clusters_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_clusters'))
    )
    op.create_index(op.f('ix_clusters_cloud_account_id'), 'clusters', ['cloud_account_id'], unique=False)
    op.create_index(op.f('ix_clusters_organization_id'), 'clusters', ['organization_id'], unique=False)
    op.create_index(op.f('ix_clusters_status'), 'clusters', ['status'], unique=False)
    op.create_index('uq_clusters_org_name_active', 'clusters', ['organization_id', 'name'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'), sqlite_where=sa.text('deleted_at IS NULL'))
    op.create_table('cluster_events',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('cluster_id', sa.Uuid(), nullable=False),
    sa.Column('node_name', sa.String(length=63), nullable=True),
    sa.Column('event_type', sa.String(length=60), nullable=False),
    sa.Column('severity', sa.String(length=20), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['cluster_id'], ['clusters.id'], name=op.f('fk_cluster_events_cluster_id_clusters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_cluster_events_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cluster_events'))
    )
    op.create_index('ix_cluster_events_cluster_created', 'cluster_events', ['cluster_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_cluster_events_organization_id'), 'cluster_events', ['organization_id'], unique=False)
    op.create_table('cluster_nodes',
    sa.Column('cluster_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=63), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('instance_id', sa.String(length=100), nullable=True),
    sa.Column('instance_name', sa.String(length=63), nullable=True),
    sa.Column('hostname', sa.String(length=255), nullable=True),
    sa.Column('private_ip', sa.String(length=45), nullable=True),
    sa.Column('zone', sa.String(length=50), nullable=False),
    sa.Column('role', sa.String(length=60), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('health', sa.String(length=20), nullable=False),
    sa.Column('health_reasons', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('instance_status', sa.String(length=20), nullable=True),
    sa.Column('engine_version', sa.String(length=40), nullable=True),
    sa.Column('agent_version', sa.String(length=40), nullable=True),
    sa.Column('agent_token_hash', sa.String(length=64), nullable=True),
    sa.Column('agent_registered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('agent_last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_report', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('last_report_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('report_source', sa.String(length=20), nullable=True),
    sa.Column('bootstrap_status', sa.String(length=20), nullable=True),
    sa.Column('bootstrap_message', sa.Text(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['cluster_id'], ['clusters.id'], name=op.f('fk_cluster_nodes_cluster_id_clusters'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cluster_nodes')),
    sa.UniqueConstraint('agent_token_hash', name=op.f('uq_cluster_nodes_agent_token_hash'))
    )
    op.create_index(op.f('ix_cluster_nodes_cluster_id'), 'cluster_nodes', ['cluster_id'], unique=False)
    op.create_index('uq_cluster_nodes_cluster_name_active', 'cluster_nodes', ['cluster_id', 'name'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'), sqlite_where=sa.text('deleted_at IS NULL'))
    op.create_table('metric_samples',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('cluster_id', sa.Uuid(), nullable=False),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('values', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['cluster_id'], ['clusters.id'], name=op.f('fk_metric_samples_cluster_id_clusters'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_metric_samples'))
    )
    op.create_index('ix_metric_samples_cluster_captured', 'metric_samples', ['cluster_id', 'captured_at'], unique=False)
    op.create_table('operations',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('cluster_id', sa.Uuid(), nullable=True),
    sa.Column('operation_type', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('current_step', sa.String(length=60), nullable=True),
    sa.Column('progress', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_code', sa.String(length=80), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('metadata', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('idempotency_key', sa.String(length=200), nullable=True),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('worker_id', sa.String(length=100), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_enqueued_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['cluster_id'], ['clusters.id'], name=op.f('fk_operations_cluster_id_clusters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_operations_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_operations_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations'))
    )
    op.create_index(op.f('ix_operations_cluster_id'), 'operations', ['cluster_id'], unique=False)
    op.create_index('ix_operations_org_created', 'operations', ['organization_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_operations_status'), 'operations', ['status'], unique=False)
    op.create_index('uq_operations_active_mutation', 'operations', ['cluster_id'], unique=True, postgresql_where=sa.text("status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED') AND operation_type <> 'HEALTH_CHECK'"), sqlite_where=sa.text("status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED') AND operation_type <> 'HEALTH_CHECK'"))
    op.create_index('uq_operations_org_idempotency_key', 'operations', ['organization_id', 'idempotency_key'], unique=True, postgresql_where=sa.text('idempotency_key IS NOT NULL'), sqlite_where=sa.text('idempotency_key IS NOT NULL'))
    op.create_table('agent_commands',
    sa.Column('cluster_id', sa.Uuid(), nullable=False),
    sa.Column('node_id', sa.Uuid(), nullable=False),
    sa.Column('operation_id', sa.Uuid(), nullable=True),
    sa.Column('command', sa.String(length=60), nullable=False),
    sa.Column('args', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['cluster_id'], ['clusters.id'], name=op.f('fk_agent_commands_cluster_id_clusters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['node_id'], ['cluster_nodes.id'], name=op.f('fk_agent_commands_node_id_cluster_nodes'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_commands'))
    )
    op.create_index(op.f('ix_agent_commands_cluster_id'), 'agent_commands', ['cluster_id'], unique=False)
    op.create_index(op.f('ix_agent_commands_node_id'), 'agent_commands', ['node_id'], unique=False)

def downgrade() -> None:
    
    op.drop_index(op.f('ix_agent_commands_node_id'), table_name='agent_commands')
    op.drop_index(op.f('ix_agent_commands_cluster_id'), table_name='agent_commands')
    op.drop_table('agent_commands')
    op.drop_index('uq_operations_org_idempotency_key', table_name='operations', postgresql_where=sa.text('idempotency_key IS NOT NULL'), sqlite_where=sa.text('idempotency_key IS NOT NULL'))
    op.drop_index('uq_operations_active_mutation', table_name='operations', postgresql_where=sa.text("status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED') AND operation_type <> 'HEALTH_CHECK'"), sqlite_where=sa.text("status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED') AND operation_type <> 'HEALTH_CHECK'"))
    op.drop_index(op.f('ix_operations_status'), table_name='operations')
    op.drop_index('ix_operations_org_created', table_name='operations')
    op.drop_index(op.f('ix_operations_cluster_id'), table_name='operations')
    op.drop_table('operations')
    op.drop_index('ix_metric_samples_cluster_captured', table_name='metric_samples')
    op.drop_table('metric_samples')
    op.drop_index('uq_cluster_nodes_cluster_name_active', table_name='cluster_nodes', postgresql_where=sa.text('deleted_at IS NULL'), sqlite_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_cluster_nodes_cluster_id'), table_name='cluster_nodes')
    op.drop_table('cluster_nodes')
    op.drop_index(op.f('ix_cluster_events_organization_id'), table_name='cluster_events')
    op.drop_index('ix_cluster_events_cluster_created', table_name='cluster_events')
    op.drop_table('cluster_events')
    op.drop_index('uq_clusters_org_name_active', table_name='clusters', postgresql_where=sa.text('deleted_at IS NULL'), sqlite_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_clusters_status'), table_name='clusters')
    op.drop_index(op.f('ix_clusters_organization_id'), table_name='clusters')
    op.drop_index(op.f('ix_clusters_cloud_account_id'), table_name='clusters')
    op.drop_table('clusters')
    op.drop_index(op.f('ix_organization_members_user_id'), table_name='organization_members')
    op.drop_index(op.f('ix_organization_members_organization_id'), table_name='organization_members')
    op.drop_table('organization_members')
    op.drop_index(op.f('ix_cloud_accounts_organization_id'), table_name='cloud_accounts')
    op.drop_table('cloud_accounts')
    op.drop_index('ix_audit_logs_org_created', table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_table('users')
    op.drop_table('organizations')
    op.drop_index(op.f('ix_mock_instances_cluster_id'), table_name='mock_instances')
    op.drop_table('mock_instances')
