"""model registry governance (V5 Phase E): gated registry for self-hosted / fine-tuned model artifacts

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-10
Additive only.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('model_registry_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('provider_name', sa.String(length=60), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('base_model', sa.String(length=120), nullable=False),
    sa.Column('license', sa.String(length=120), nullable=False),
    sa.Column('attestations', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('model_card', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('capabilities', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('usd_per_second', sa.Float(), nullable=False),
    sa.Column('eval_run_id', sa.UUID(), nullable=True),
    sa.Column('evaluation', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('approvals', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider_name', 'version', name='uq_model_registry_version')
    )


def downgrade() -> None:
    op.drop_table('model_registry_versions')
