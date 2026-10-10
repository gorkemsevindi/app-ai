"""evaluation harness + scheduler run log (V4 Stage E)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-10
Additive only.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('benchmark_sets',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('frozen', sa.Boolean(), nullable=False),
    sa.Column('rights_note', sa.String(length=300), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', 'version', name='uq_benchmark_set_version')
    )
    op.create_table('scheduled_runs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('task', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_scheduled_runs_task'), 'scheduled_runs', ['task'], unique=False)
    op.create_table('benchmark_cases',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('set_id', sa.UUID(), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('category', sa.String(length=40), nullable=False),
    sa.Column('prompt', sa.String(length=1500), nullable=False),
    sa.Column('duration_s', sa.Integer(), nullable=False),
    sa.Column('aspect_ratio', sa.String(length=8), nullable=False),
    sa.ForeignKeyConstraint(['set_id'], ['benchmark_sets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('set_id', 'key', name='uq_benchmark_case_key')
    )
    op.create_index(op.f('ix_benchmark_cases_set_id'), 'benchmark_cases', ['set_id'], unique=False)
    op.create_table('benchmark_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('set_id', sa.UUID(), nullable=False),
    sa.Column('providers', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['set_id'], ['benchmark_sets.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_benchmark_runs_set_id'), 'benchmark_runs', ['set_id'], unique=False)
    op.create_table('benchmark_results',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.String(length=60), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('adherence', sa.Integer(), nullable=True),
    sa.Column('quality', sa.Integer(), nullable=True),
    sa.Column('reviewer_id', sa.UUID(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.String(length=300), nullable=True),
    sa.ForeignKeyConstraint(['case_id'], ['benchmark_cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['benchmark_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'case_id', 'provider', name='uq_benchmark_result')
    )
    op.create_index(op.f('ix_benchmark_results_job_id'), 'benchmark_results', ['job_id'], unique=False)
    op.create_index(op.f('ix_benchmark_results_run_id'), 'benchmark_results', ['run_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_benchmark_results_run_id'), table_name='benchmark_results')
    op.drop_index(op.f('ix_benchmark_results_job_id'), table_name='benchmark_results')
    op.drop_table('benchmark_results')
    op.drop_index(op.f('ix_benchmark_runs_set_id'), table_name='benchmark_runs')
    op.drop_table('benchmark_runs')
    op.drop_index(op.f('ix_benchmark_cases_set_id'), table_name='benchmark_cases')
    op.drop_table('benchmark_cases')
    op.drop_index(op.f('ix_scheduled_runs_task'), table_name='scheduled_runs')
    op.drop_table('scheduled_runs')
    op.drop_table('benchmark_sets')
