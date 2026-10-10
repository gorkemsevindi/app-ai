"""character identity ecosystem (V6): characters, identity versions, assets, QC reports, rights claims, voice
profiles, project cast snapshots, marketplace listings, licence grants, usage events; reports.character_id

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-10
Additive only. The job_kind enum value cannot be removed on downgrade (PostgreSQL); it is harmless if unused.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'character_asset'")
    op.create_table('character_usage_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('grant_id', sa.UUID(), nullable=True),
    sa.Column('identity_version_id', sa.UUID(), nullable=False),
    sa.Column('seconds', sa.Integer(), nullable=False),
    sa.Column('license_credits', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'character_id', name='uq_character_usage_job')
    )
    op.create_index(op.f('ix_character_usage_events_character_id'), 'character_usage_events', ['character_id'], unique=False)
    op.create_index(op.f('ix_character_usage_events_grant_id'), 'character_usage_events', ['grant_id'], unique=False)
    op.create_index(op.f('ix_character_usage_events_job_id'), 'character_usage_events', ['job_id'], unique=False)
    op.create_index(op.f('ix_character_usage_events_owner_id'), 'character_usage_events', ['owner_id'], unique=False)
    op.create_index(op.f('ix_character_usage_events_user_id'), 'character_usage_events', ['user_id'], unique=False)
    op.create_table('characters',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('creator_id', sa.UUID(), nullable=False),
    sa.Column('handle', sa.String(length=32), nullable=False),
    sa.Column('display_name', sa.String(length=60), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('origin', sa.String(length=16), nullable=False),
    sa.Column('identity_profile_id', sa.UUID(), nullable=True),
    sa.Column('current_version_id', sa.UUID(), nullable=True),
    sa.Column('locked_version_id', sa.UUID(), nullable=True),
    sa.Column('moderation_status', sa.String(length=16), nullable=False),
    sa.Column('moderation_note', sa.String(length=300), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['creator_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('creator_id', 'handle', name='uq_character_creator_handle')
    )
    op.create_index(op.f('ix_characters_creator_id'), 'characters', ['creator_id'], unique=False)
    op.create_table('character_identity_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('major', sa.Integer(), nullable=False),
    sa.Column('minor', sa.Integer(), nullable=False),
    sa.Column('parent_version_id', sa.UUID(), nullable=True),
    sa.Column('change_kind', sa.String(length=10), nullable=False),
    sa.Column('original_prompt', sa.Text(), nullable=False),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('master_asset_id', sa.UUID(), nullable=True),
    sa.Column('package', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('package_checksum', sa.String(length=64), nullable=True),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('character_id', 'major', 'minor', name='uq_character_version')
    )
    op.create_index(op.f('ix_character_identity_versions_character_id'), 'character_identity_versions', ['character_id'], unique=False)
    op.create_table('character_listings',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('identity_version_id', sa.UUID(), nullable=False),
    sa.Column('visibility', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('terms', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('terms_version', sa.Integer(), nullable=False),
    sa.Column('certification', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('review_note', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('character_id')
    )
    op.create_index(op.f('ix_character_listings_owner_id'), 'character_listings', ['owner_id'], unique=False)
    op.create_table('character_quality_reports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('identity_version_id', sa.UUID(), nullable=False),
    sa.Column('scope', sa.String(length=10), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('method', sa.String(length=20), nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('thresholds', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('verdict', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_character_quality_reports_character_id'), 'character_quality_reports', ['character_id'], unique=False)
    op.create_index(op.f('ix_character_quality_reports_identity_version_id'), 'character_quality_reports', ['identity_version_id'], unique=False)
    op.create_index(op.f('ix_character_quality_reports_job_id'), 'character_quality_reports', ['job_id'], unique=False)
    op.create_table('character_rights_claims',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('origin', sa.String(length=16), nullable=False),
    sa.Column('declaration', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('terms_version', sa.String(length=32), nullable=False),
    sa.Column('consent_receipt_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_character_rights_claims_character_id'), 'character_rights_claims', ['character_id'], unique=False)
    op.create_table('character_voice_profiles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('provider', sa.String(length=60), nullable=False),
    sa.Column('voice_id', sa.String(length=120), nullable=False),
    sa.Column('languages', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('territories', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('allowed_use', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('style', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('character_id', 'version', name='uq_character_voice_version')
    )
    op.create_index(op.f('ix_character_voice_profiles_character_id'), 'character_voice_profiles', ['character_id'], unique=False)
    op.create_table('project_cast_members',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('identity_version_id', sa.UUID(), nullable=False),
    sa.Column('voice_profile_id', sa.UUID(), nullable=True),
    sa.Column('alias', sa.String(length=40), nullable=False),
    sa.Column('lock_mode', sa.String(length=10), nullable=False),
    sa.Column('allowed_variants', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('grant_id', sa.UUID(), nullable=True),
    sa.Column('added_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['project_id'], ['studio_projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_project_cast_members_character_id'), 'project_cast_members', ['character_id'], unique=False)
    op.create_index(op.f('ix_project_cast_members_project_id'), 'project_cast_members', ['project_id'], unique=False)
    op.create_index('uq_cast_alias_active', 'project_cast_members', ['project_id', 'alias'], unique=True, postgresql_where=sa.text('removed_at IS NULL'))
    op.create_table('character_assets',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('identity_version_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('view_key', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('provider', sa.String(length=60), nullable=True),
    sa.Column('model', sa.String(length=120), nullable=True),
    sa.Column('seed', sa.BigInteger(), nullable=True),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('reference_asset_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('qc', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('creator_review', sa.String(length=12), nullable=True),
    sa.Column('superseded_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['identity_version_id'], ['character_identity_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_character_assets_character_id'), 'character_assets', ['character_id'], unique=False)
    op.create_index(op.f('ix_character_assets_identity_version_id'), 'character_assets', ['identity_version_id'], unique=False)
    op.create_index(op.f('ix_character_assets_job_id'), 'character_assets', ['job_id'], unique=False)
    op.create_table('character_license_grants',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('listing_id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('licensee_id', sa.UUID(), nullable=False),
    sa.Column('terms_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('terms_version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoke_reason', sa.String(length=300), nullable=True),
    sa.Column('idempotency_key', sa.String(length=160), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['licensee_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['listing_id'], ['character_listings.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index(op.f('ix_character_license_grants_character_id'), 'character_license_grants', ['character_id'], unique=False)
    op.create_index(op.f('ix_character_license_grants_licensee_id'), 'character_license_grants', ['licensee_id'], unique=False)
    op.create_index(op.f('ix_character_license_grants_listing_id'), 'character_license_grants', ['listing_id'], unique=False)
    op.add_column('reports', sa.Column('character_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_reports_character_id'), 'reports', ['character_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_reports_character_id'), table_name='reports')
    op.drop_column('reports', 'character_id')
    op.drop_index(op.f('ix_character_license_grants_listing_id'), table_name='character_license_grants')
    op.drop_index(op.f('ix_character_license_grants_licensee_id'), table_name='character_license_grants')
    op.drop_index(op.f('ix_character_license_grants_character_id'), table_name='character_license_grants')
    op.drop_table('character_license_grants')
    op.drop_index(op.f('ix_character_assets_job_id'), table_name='character_assets')
    op.drop_index(op.f('ix_character_assets_identity_version_id'), table_name='character_assets')
    op.drop_index(op.f('ix_character_assets_character_id'), table_name='character_assets')
    op.drop_table('character_assets')
    op.drop_index('uq_cast_alias_active', table_name='project_cast_members', postgresql_where=sa.text('removed_at IS NULL'))
    op.drop_index(op.f('ix_project_cast_members_project_id'), table_name='project_cast_members')
    op.drop_index(op.f('ix_project_cast_members_character_id'), table_name='project_cast_members')
    op.drop_table('project_cast_members')
    op.drop_index(op.f('ix_character_voice_profiles_character_id'), table_name='character_voice_profiles')
    op.drop_table('character_voice_profiles')
    op.drop_index(op.f('ix_character_rights_claims_character_id'), table_name='character_rights_claims')
    op.drop_table('character_rights_claims')
    op.drop_index(op.f('ix_character_quality_reports_job_id'), table_name='character_quality_reports')
    op.drop_index(op.f('ix_character_quality_reports_identity_version_id'), table_name='character_quality_reports')
    op.drop_index(op.f('ix_character_quality_reports_character_id'), table_name='character_quality_reports')
    op.drop_table('character_quality_reports')
    op.drop_index(op.f('ix_character_listings_owner_id'), table_name='character_listings')
    op.drop_table('character_listings')
    op.drop_index(op.f('ix_character_identity_versions_character_id'), table_name='character_identity_versions')
    op.drop_table('character_identity_versions')
    op.drop_index(op.f('ix_characters_creator_id'), table_name='characters')
    op.drop_table('characters')
    op.drop_index(op.f('ix_character_usage_events_user_id'), table_name='character_usage_events')
    op.drop_index(op.f('ix_character_usage_events_owner_id'), table_name='character_usage_events')
    op.drop_index(op.f('ix_character_usage_events_job_id'), table_name='character_usage_events')
    op.drop_index(op.f('ix_character_usage_events_grant_id'), table_name='character_usage_events')
    op.drop_index(op.f('ix_character_usage_events_character_id'), table_name='character_usage_events')
    op.drop_table('character_usage_events')
