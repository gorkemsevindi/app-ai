"""V7 AI cinema & short drama factory: productions, story branches, episodes, story bibles, episode snapshots,
continuity findings, cost estimates, budget authorizations, production events (outbox), life-story sessions;
studio_projects gains production_episode_id / limits / content_policy

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-10
Additive only.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('continuity_findings',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('episode_id', sa.UUID(), nullable=False),
    sa.Column('studio_version_id', sa.UUID(), nullable=True),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('message', sa.String(length=400), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_continuity_findings_episode_id'), 'continuity_findings', ['episode_id'], unique=False)
    op.create_table('production_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('production_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.String(length=40), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_production_events_production_id'), 'production_events', ['production_id'], unique=False)
    op.create_table('life_story_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('answers', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('chronology', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('privacy', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('production_id', sa.UUID(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_life_story_sessions_user_id'), 'life_story_sessions', ['user_id'], unique=False)
    op.create_table('productions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('logline', sa.String(length=600), nullable=False),
    sa.Column('genre', sa.String(length=40), nullable=False),
    sa.Column('audience', sa.String(length=40), nullable=False),
    sa.Column('format', sa.String(length=20), nullable=False),
    sa.Column('visual_style', sa.String(length=20), nullable=False),
    sa.Column('language', sa.String(length=8), nullable=False),
    sa.Column('aspect_ratio', sa.String(length=8), nullable=False),
    sa.Column('content_rating', sa.String(length=10), nullable=False),
    sa.Column('profile', sa.String(length=12), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('active_branch_id', sa.UUID(), nullable=True),
    sa.Column('cast', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('people_confirmed', sa.Boolean(), nullable=False),
    sa.Column('visibility', sa.String(length=10), nullable=False),
    sa.Column('policy_version', sa.String(length=20), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_productions_owner_id'), 'productions', ['owner_id'], unique=False)
    op.create_table('budget_authorizations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('production_id', sa.UUID(), nullable=False),
    sa.Column('estimate_id', sa.UUID(), nullable=True),
    sa.Column('cap_credits', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('cap_minor', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('idempotency_key', sa.String(length=160), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['production_id'], ['productions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index(op.f('ix_budget_authorizations_production_id'), 'budget_authorizations', ['production_id'], unique=False)
    op.create_table('cost_estimates',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('production_id', sa.UUID(), nullable=False),
    sa.Column('scope', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('profile', sa.String(length=12), nullable=False),
    sa.Column('profile_version', sa.Integer(), nullable=False),
    sa.Column('credits_low', sa.Integer(), nullable=False),
    sa.Column('credits_high', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('amount_low_minor', sa.BigInteger(), nullable=True),
    sa.Column('amount_high_minor', sa.BigInteger(), nullable=True),
    sa.Column('provider_usd_low', sa.Float(), nullable=True),
    sa.Column('provider_usd_high', sa.Float(), nullable=True),
    sa.Column('feasible', sa.Boolean(), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['production_id'], ['productions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cost_estimates_production_id'), 'cost_estimates', ['production_id'], unique=False)
    op.create_table('story_bibles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('production_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['production_id'], ['productions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('production_id', 'version', name='uq_story_bible_version')
    )
    op.create_index(op.f('ix_story_bibles_production_id'), 'story_bibles', ['production_id'], unique=False)
    op.create_table('story_branches',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('production_id', sa.UUID(), nullable=False),
    sa.Column('parent_branch_id', sa.UUID(), nullable=True),
    sa.Column('fork_episode', sa.Integer(), nullable=True),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['production_id'], ['productions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_story_branches_production_id'), 'story_branches', ['production_id'], unique=False)
    op.create_table('production_episodes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('production_id', sa.UUID(), nullable=False),
    sa.Column('branch_id', sa.UUID(), nullable=False),
    sa.Column('season', sa.Integer(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('synopsis', sa.String(length=2000), nullable=False),
    sa.Column('target_duration_s', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('studio_project_id', sa.UUID(), nullable=False),
    sa.Column('script', sa.Text(), nullable=True),
    sa.Column('events', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('plan_report', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('animatic_job_id', sa.UUID(), nullable=True),
    sa.Column('pilot_job_id', sa.UUID(), nullable=True),
    sa.Column('pilot_keys', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['branch_id'], ['story_branches.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['production_id'], ['productions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('branch_id', 'season', 'number', name='uq_episode_branch_number')
    )
    op.create_index(op.f('ix_production_episodes_branch_id'), 'production_episodes', ['branch_id'], unique=False)
    op.create_index(op.f('ix_production_episodes_production_id'), 'production_episodes', ['production_id'], unique=False)
    op.create_table('episode_snapshots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('episode_id', sa.UUID(), nullable=False),
    sa.Column('branch_id', sa.UUID(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('studio_version_id', sa.UUID(), nullable=True),
    sa.Column('bible_version', sa.Integer(), nullable=True),
    sa.Column('events', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['episode_id'], ['production_episodes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_episode_snapshots_episode_id'), 'episode_snapshots', ['episode_id'], unique=False)
    op.add_column('studio_projects', sa.Column('production_episode_id', sa.UUID(), nullable=True))
    op.add_column('studio_projects', sa.Column('limits', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    op.add_column('studio_projects', sa.Column('content_policy', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    op.create_index(op.f('ix_studio_projects_production_episode_id'), 'studio_projects', ['production_episode_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_studio_projects_production_episode_id'), table_name='studio_projects')
    op.drop_column('studio_projects', 'content_policy')
    op.drop_column('studio_projects', 'limits')
    op.drop_column('studio_projects', 'production_episode_id')
    op.drop_index(op.f('ix_episode_snapshots_episode_id'), table_name='episode_snapshots')
    op.drop_table('episode_snapshots')
    op.drop_index(op.f('ix_production_episodes_production_id'), table_name='production_episodes')
    op.drop_index(op.f('ix_production_episodes_branch_id'), table_name='production_episodes')
    op.drop_table('production_episodes')
    op.drop_index(op.f('ix_story_branches_production_id'), table_name='story_branches')
    op.drop_table('story_branches')
    op.drop_index(op.f('ix_story_bibles_production_id'), table_name='story_bibles')
    op.drop_table('story_bibles')
    op.drop_index(op.f('ix_cost_estimates_production_id'), table_name='cost_estimates')
    op.drop_table('cost_estimates')
    op.drop_index(op.f('ix_budget_authorizations_production_id'), table_name='budget_authorizations')
    op.drop_table('budget_authorizations')
    op.drop_index(op.f('ix_productions_owner_id'), table_name='productions')
    op.drop_table('productions')
    op.drop_index(op.f('ix_life_story_sessions_user_id'), table_name='life_story_sessions')
    op.drop_table('life_story_sessions')
    op.drop_index(op.f('ix_production_events_production_id'), table_name='production_events')
    op.drop_table('production_events')
    op.drop_index(op.f('ix_continuity_findings_episode_id'), table_name='continuity_findings')
    op.drop_table('continuity_findings')
