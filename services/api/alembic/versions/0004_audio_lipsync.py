"""audio assets for lip-sync (custom soundtracks)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-10
Additive only. Speaker timelines live in source_videos.analysis["audio"] and job specs (JSONB).
"""
import sqlalchemy as sa

from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'audio_assets',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('storage_key', sa.String(512), nullable=False),
        sa.Column('mime', sa.String(64), nullable=False),
        sa.Column('declared_size', sa.BigInteger(), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('rights_basis', sa.String(20), nullable=False),
        sa.Column('rights_attested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attestation_version', sa.String(32), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('storage_key'),
    )
    op.create_index('ix_audio_assets_user_id', 'audio_assets', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_audio_assets_user_id', table_name='audio_assets')
    op.drop_table('audio_assets')
