"""AI Studio foundations (V4 Stage B): projects, immutable versions, shot renders, characters, consent receipts

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-10
Additive. New job_kind values cannot be dropped on downgrade (PostgreSQL); harmless when unused.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _ts():
    return [sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False)]


def upgrade() -> None:
    op.execute("ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'studio_shot'")
    op.execute("ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'studio_assemble'")
    op.create_table(
        'studio_projects',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(120), nullable=False),
        sa.Column('aspect_ratio', sa.String(8), nullable=False),
        sa.Column('language', sa.String(8), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('current_version_id', sa.UUID(), nullable=True),
        sa.Column('rendered_version_id', sa.UUID(), nullable=True),
        sa.Column('output_job_id', sa.UUID(), nullable=True),
        sa.Column('budget_credits', sa.Integer(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_studio_projects_user_id', 'studio_projects', ['user_id'])
    op.create_table(
        'studio_project_versions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('parent_version_id', sa.UUID(), nullable=True),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('brief', JSONB, nullable=False),
        sa.Column('storyboard', JSONB, nullable=False),
        sa.Column('director', JSONB, nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['studio_projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'version', name='uq_studio_version'),
    )
    op.create_index('ix_studio_project_versions_project_id', 'studio_project_versions', ['project_id'])
    # Versions are immutable snapshots (deletes only via project cascade).
    op.execute("""
    CREATE OR REPLACE FUNCTION studio_version_immutable() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION 'studio project versions are immutable';
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE TRIGGER trg_studio_version_immutable BEFORE UPDATE ON studio_project_versions
    FOR EACH ROW EXECUTE FUNCTION studio_version_immutable();
    """)
    op.create_table(
        'studio_shot_renders',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('shot_key', sa.String(40), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('job_id', sa.UUID(), nullable=True),
        sa.Column('video_key', sa.String(512), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['studio_projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['job_id'], ['generation_jobs.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'content_hash', name='uq_studio_shot_hash'),
    )
    op.create_index('ix_studio_shot_renders_project_id', 'studio_shot_renders', ['project_id'])
    op.create_table(
        'studio_characters',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=True),
        sa.Column('name', sa.String(60), nullable=False),
        sa.Column('description', sa.String(500), nullable=False),
        sa.Column('traits', JSONB, nullable=False),
        sa.Column('identity_profile_id', sa.UUID(), nullable=True),
        sa.Column('voice_permission', sa.String(20), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['studio_projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['identity_profile_id'], ['identity_profiles.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_studio_characters_user_id', 'studio_characters', ['user_id'])
    op.create_table(
        'consent_receipts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('subject_type', sa.String(30), nullable=False),
        sa.Column('subject_id', sa.UUID(), nullable=False),
        sa.Column('scope', JSONB, nullable=False),
        sa.Column('terms_version', sa.String(32), nullable=False),
        sa.Column('statement', sa.String(300), nullable=False),
        sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_consent_receipts_user_id', 'consent_receipts', ['user_id'])
    op.create_index('ix_consent_receipts_subject_id', 'consent_receipts', ['subject_id'])
    op.add_column('generation_jobs', sa.Column('studio_project_id', sa.UUID(), nullable=True))
    op.create_index('ix_generation_jobs_studio_project_id', 'generation_jobs', ['studio_project_id'])


def downgrade() -> None:
    op.drop_index('ix_generation_jobs_studio_project_id', table_name='generation_jobs')
    op.drop_column('generation_jobs', 'studio_project_id')
    op.drop_index('ix_consent_receipts_subject_id', table_name='consent_receipts')
    op.drop_index('ix_consent_receipts_user_id', table_name='consent_receipts')
    op.drop_table('consent_receipts')
    op.drop_index('ix_studio_characters_user_id', table_name='studio_characters')
    op.drop_table('studio_characters')
    op.drop_index('ix_studio_shot_renders_project_id', table_name='studio_shot_renders')
    op.drop_table('studio_shot_renders')
    op.execute('DROP TRIGGER IF EXISTS trg_studio_version_immutable ON studio_project_versions')
    op.execute('DROP FUNCTION IF EXISTS studio_version_immutable()')
    op.drop_index('ix_studio_project_versions_project_id', table_name='studio_project_versions')
    op.drop_table('studio_project_versions')
    op.drop_index('ix_studio_projects_user_id', table_name='studio_projects')
    op.drop_table('studio_projects')
