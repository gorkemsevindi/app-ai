"""learning foundation (V5 Phase B): consent records, content-free learning events, technical memory aggregates

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-10
Additive. consent_records is append-only (database trigger).
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('model_performance_aggregates',
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('provider', sa.String(length=60), nullable=False),
    sa.Column('feature', sa.String(length=40), nullable=False),
    sa.Column('creative_mode', sa.String(length=16), nullable=False),
    sa.Column('jobs', sa.Integer(), nullable=False),
    sa.Column('successes', sa.Integer(), nullable=False),
    sa.Column('p50_latency_s', sa.Float(), nullable=True),
    sa.Column('p95_latency_s', sa.Float(), nullable=True),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.Column('ratings', sa.Integer(), nullable=False),
    sa.Column('rating_mean', sa.Float(), nullable=True),
    sa.Column('quality', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('errors', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('day', 'provider', 'feature', 'creative_mode')
    )
    op.create_table('consent_records',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('purpose', sa.String(length=32), nullable=False),
    sa.Column('granted', sa.Boolean(), nullable=False),
    sa.Column('policy_version', sa.String(length=32), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_consent_records_user_id'), 'consent_records', ['user_id'], unique=False)
    op.create_table('learning_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('schema_version', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('feature', sa.String(length=40), nullable=False),
    sa.Column('intent_category', sa.String(length=40), nullable=True),
    sa.Column('creative_mode', sa.String(length=16), nullable=True),
    sa.Column('provider', sa.String(length=60), nullable=True),
    sa.Column('prompt_strategy', sa.String(length=40), nullable=True),
    sa.Column('resolution', sa.String(length=16), nullable=True),
    sa.Column('duration_s', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=True),
    sa.Column('success', sa.Boolean(), nullable=True),
    sa.Column('error_code', sa.String(length=60), nullable=True),
    sa.Column('retries', sa.Integer(), nullable=True),
    sa.Column('latency_s', sa.Float(), nullable=True),
    sa.Column('credits', sa.Integer(), nullable=True),
    sa.Column('est_cost_usd', sa.Float(), nullable=True),
    sa.Column('actual_cost_usd', sa.Float(), nullable=True),
    sa.Column('quality', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('rating', sa.Integer(), nullable=True),
    sa.Column('reasons', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('regenerated', sa.Boolean(), nullable=False),
    sa.Column('consent', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('purpose', sa.String(length=32), nullable=False),
    sa.Column('provenance', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('retention_until', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'kind', name='uq_learning_event_job_kind')
    )
    op.create_index(op.f('ix_learning_events_created_at'), 'learning_events', ['created_at'], unique=False)
    op.create_index(op.f('ix_learning_events_job_id'), 'learning_events', ['job_id'], unique=False)
    op.create_index(op.f('ix_learning_events_user_id'), 'learning_events', ['user_id'], unique=False)
    op.execute("CREATE TRIGGER trg_consent_records_immutable BEFORE UPDATE OR DELETE ON consent_records "
               "FOR EACH ROW EXECUTE FUNCTION credit_ledger_immutable()")


def downgrade() -> None:
    op.execute('DROP TRIGGER IF EXISTS trg_consent_records_immutable ON consent_records')
    op.drop_index(op.f('ix_learning_events_user_id'), table_name='learning_events')
    op.drop_index(op.f('ix_learning_events_job_id'), table_name='learning_events')
    op.drop_index(op.f('ix_learning_events_created_at'), table_name='learning_events')
    op.drop_table('learning_events')
    op.drop_index(op.f('ix_consent_records_user_id'), table_name='consent_records')
    op.drop_table('consent_records')
    op.drop_table('model_performance_aggregates')
