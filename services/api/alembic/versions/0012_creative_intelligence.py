"""creative intelligence (V5 Phase C): prompt strategy registry, similarity audits, creative metadata on versions

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-10
Additive only.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('prompt_strategies',
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=False),
    sa.Column('config_sha256', sa.String(length=64), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('similarity_audits',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('project_id', sa.UUID(), nullable=True),
    sa.Column('subject', sa.String(length=20), nullable=False),
    sa.Column('category', sa.String(length=40), nullable=False),
    sa.Column('method', sa.String(length=30), nullable=False),
    sa.Column('threshold', sa.Float(), nullable=False),
    sa.Column('top_kind', sa.String(length=20), nullable=True),
    sa.Column('top_ref', sa.String(length=64), nullable=True),
    sa.Column('top_score', sa.Float(), nullable=False),
    sa.Column('decision', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_similarity_audits_created_at'), 'similarity_audits', ['created_at'], unique=False)
    op.create_index(op.f('ix_similarity_audits_user_id'), 'similarity_audits', ['user_id'], unique=False)
    op.add_column('studio_project_versions', sa.Column('creative', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))


def downgrade() -> None:
    op.drop_column('studio_project_versions', 'creative')
    op.drop_index(op.f('ix_similarity_audits_user_id'), table_name='similarity_audits')
    op.drop_index(op.f('ix_similarity_audits_created_at'), table_name='similarity_audits')
    op.drop_table('similarity_audits')
    op.drop_table('prompt_strategies')
