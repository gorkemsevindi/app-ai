"""V8 unified creative studio: canonical editor projects, append-only revisions, editor assets;
job_kind.editor_render

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-10
Additive only (the enum value stays on downgrade, harmless if unused).
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'editor_render'")
    op.create_table('editor_assets',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('mime', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('license', sa.String(length=200), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_editor_assets_owner_id'), 'editor_assets', ['owner_id'], unique=False)
    op.create_table('editor_projects',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.String(length=10), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('production_id', sa.UUID(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_editor_projects_owner_id'), 'editor_projects', ['owner_id'], unique=False)
    op.create_table('editor_revisions',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('base_revision', sa.Integer(), nullable=False),
    sa.Column('commands', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('idempotency_key', sa.String(length=120), nullable=False),
    sa.Column('client', sa.String(length=20), nullable=True),
    sa.Column('author_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['editor_projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'idempotency_key', name='uq_editor_revision_idem'),
    sa.UniqueConstraint('project_id', 'revision', name='uq_editor_revision')
    )
    op.create_index(op.f('ix_editor_revisions_project_id'), 'editor_revisions', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_editor_revisions_project_id'), table_name='editor_revisions')
    op.drop_table('editor_revisions')
    op.drop_index(op.f('ix_editor_projects_owner_id'), table_name='editor_projects')
    op.drop_table('editor_projects')
    op.drop_index(op.f('ix_editor_assets_owner_id'), table_name='editor_assets')
    op.drop_table('editor_assets')
