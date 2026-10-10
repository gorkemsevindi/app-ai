"""adaptive policies (V5 Phase D): versioned learning policies + sticky experiment assignments

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-10
Additive only.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('learning_policy_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('rollout_pct', sa.Integer(), nullable=False),
    sa.Column('evaluation', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('history', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kind', 'version', name='uq_learning_policy_version')
    )
    op.create_table('experiment_assignments',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('experiment_key', sa.String(length=80), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('variant', sa.String(length=20), nullable=False),
    sa.Column('policy_version_id', sa.UUID(), nullable=True),
    sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('experiment_key', 'user_id', name='uq_experiment_assignment')
    )
    op.create_index(op.f('ix_experiment_assignments_user_id'), 'experiment_assignments', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_experiment_assignments_user_id'), table_name='experiment_assignments')
    op.drop_table('experiment_assignments')
    op.drop_table('learning_policy_versions')
