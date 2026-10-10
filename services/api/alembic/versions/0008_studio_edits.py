"""AI Studio edit operations (V4 Stage C)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-10
Additive only.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        'studio_edit_operations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('base_version_id', sa.UUID(), nullable=False),
        sa.Column('result_version_id', sa.UUID(), nullable=True),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('instruction', sa.String(1000), nullable=True),
        sa.Column('editor', JSONB, nullable=False),
        sa.Column('ops', JSONB, nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('clarification', sa.String(500), nullable=True),
        sa.Column('preview', JSONB, nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['studio_projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_studio_edit_operations_project_id', 'studio_edit_operations', ['project_id'])


def downgrade() -> None:
    op.drop_index('ix_studio_edit_operations_project_id', table_name='studio_edit_operations')
    op.drop_table('studio_edit_operations')
