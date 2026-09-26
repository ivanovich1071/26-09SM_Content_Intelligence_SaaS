"""saas core

Revision ID: 0001
Revises: 
Create Date: 2026-09-26 13:05:31.492597
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('organizations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=80), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organizations')),
    sa.UniqueConstraint('slug', name=op.f('uq_organizations_slug'))
    )
    op.create_table('plans',
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('price_month_usd', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('limits', sa.JSON(), nullable=False),
    sa.Column('sort', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('code', name=op.f('pk_plans'))
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=200), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('is_superadmin', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_table('invitations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('role', sa.Enum('owner', 'admin', 'member', 'viewer', name='member_role'), nullable=False),
    sa.Column('invited_by', sa.Integer(), nullable=True),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['invited_by'], ['users.id'], name=op.f('fk_invitations_invited_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_invitations_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invitations')),
    sa.UniqueConstraint('organization_id', 'email', name='uq_invitations_org_email')
    )
    op.create_index(op.f('ix_invitations_email'), 'invitations', ['email'], unique=False)
    op.create_index(op.f('ix_invitations_organization_id'), 'invitations', ['organization_id'], unique=False)
    op.create_table('jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=60), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'collecting', 'analyzing', 'generating', 'validating', 'completed', 'failed', 'cancelled', name='job_status'), nullable=False),
    sa.Column('progress', sa.Integer(), nullable=False),
    sa.Column('params', sa.JSON(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_jobs_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_jobs_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    op.create_index(op.f('ix_jobs_kind'), 'jobs', ['kind'], unique=False)
    op.create_index(op.f('ix_jobs_organization_id'), 'jobs', ['organization_id'], unique=False)
    op.create_table('memberships',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.Enum('owner', 'admin', 'member', 'viewer', name='member_role'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_memberships_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_memberships_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_memberships')),
    sa.UniqueConstraint('organization_id', 'user_id', name='uq_memberships_org_user')
    )
    op.create_index(op.f('ix_memberships_organization_id'), 'memberships', ['organization_id'], unique=False)
    op.create_index(op.f('ix_memberships_user_id'), 'memberships', ['user_id'], unique=False)
    op.create_table('subscriptions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('plan_code', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('current_period_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_subscriptions_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['plan_code'], ['plans.code'], name=op.f('fk_subscriptions_plan_code_plans')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_subscriptions')),
    sa.UniqueConstraint('organization_id', name=op.f('uq_subscriptions_organization_id'))
    )
    op.create_table('llm_requests',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('task', sa.String(length=20), nullable=False),
    sa.Column('operation', sa.String(length=80), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=False),
    sa.Column('model', sa.String(length=120), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('cost_usd', sa.Numeric(precision=12, scale=6), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('ok', sa.Boolean(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_llm_requests_job_id_jobs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_llm_requests_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_llm_requests'))
    )
    op.create_index(op.f('ix_llm_requests_at'), 'llm_requests', ['at'], unique=False)
    op.create_index(op.f('ix_llm_requests_organization_id'), 'llm_requests', ['organization_id'], unique=False)
    op.create_table('usage_events',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('metric', sa.String(length=60), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=14, scale=6), nullable=False),
    sa.Column('operation', sa.String(length=80), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=True),
    sa.Column('model', sa.String(length=120), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('estimated_cost_usd', sa.Numeric(precision=12, scale=6), nullable=True),
    sa.Column('source', sa.String(length=80), nullable=True),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_usage_events_job_id_jobs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_usage_events_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_usage_events'))
    )
    op.create_index(op.f('ix_usage_events_at'), 'usage_events', ['at'], unique=False)
    op.create_index(op.f('ix_usage_events_metric'), 'usage_events', ['metric'], unique=False)
    op.create_index(op.f('ix_usage_events_organization_id'), 'usage_events', ['organization_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_usage_events_organization_id'), table_name='usage_events')
    op.drop_index(op.f('ix_usage_events_metric'), table_name='usage_events')
    op.drop_index(op.f('ix_usage_events_at'), table_name='usage_events')
    op.drop_table('usage_events')
    op.drop_index(op.f('ix_llm_requests_organization_id'), table_name='llm_requests')
    op.drop_index(op.f('ix_llm_requests_at'), table_name='llm_requests')
    op.drop_table('llm_requests')
    op.drop_table('subscriptions')
    op.drop_index(op.f('ix_memberships_user_id'), table_name='memberships')
    op.drop_index(op.f('ix_memberships_organization_id'), table_name='memberships')
    op.drop_table('memberships')
    op.drop_index(op.f('ix_jobs_organization_id'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_kind'), table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_invitations_organization_id'), table_name='invitations')
    op.drop_index(op.f('ix_invitations_email'), table_name='invitations')
    op.drop_table('invitations')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_table('plans')
    op.drop_table('organizations')
    sa.Enum(name='job_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='member_role').drop(op.get_bind(), checkfirst=True)
