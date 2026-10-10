"""creator economy (V4 Stage D): creator profiles, versioned revenue policies, append-only creator earnings,
settlements with snapshots, risk holds, licensed AI actor listings/licences/usage events

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-10
Additive. Money tables are append-only/immutable at the database level (same trigger as the credit ledger).
The new ledger_reason value cannot be removed on downgrade (PostgreSQL); harmless when unused.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


IMMUTABLE = ("creator_earnings", "revenue_policies", "settlement_items", "license_usage_events")


def upgrade() -> None:
    op.execute("ALTER TYPE ledger_reason ADD VALUE IF NOT EXISTS 'license_fee'")
    op.create_table('revenue_policies',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('effective_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version')
    )
    op.create_table('creator_earnings',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('creator_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('amount_micros', sa.BigInteger(), nullable=False),
    sa.Column('gross_basis_micros', sa.BigInteger(), nullable=False),
    sa.Column('policy_id', sa.UUID(), nullable=True),
    sa.Column('policy_version', sa.Integer(), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('template_id', sa.UUID(), nullable=True),
    sa.Column('attribution_id', sa.UUID(), nullable=True),
    sa.Column('license_id', sa.UUID(), nullable=True),
    sa.Column('payer_id', sa.UUID(), nullable=True),
    sa.Column('settlement_id', sa.UUID(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=200), nullable=False),
    sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['creator_id'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['policy_id'], ['revenue_policies.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index(op.f('ix_creator_earnings_created_at'), 'creator_earnings', ['created_at'], unique=False)
    op.create_index(op.f('ix_creator_earnings_creator_id'), 'creator_earnings', ['creator_id'], unique=False)
    op.create_index(op.f('ix_creator_earnings_job_id'), 'creator_earnings', ['job_id'], unique=False)
    op.create_index(op.f('ix_creator_earnings_payer_id'), 'creator_earnings', ['payer_id'], unique=False)
    op.create_index(op.f('ix_creator_earnings_template_id'), 'creator_earnings', ['template_id'], unique=False)
    op.create_table('creator_profiles',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('handle', sa.String(length=32), nullable=False),
    sa.Column('display_name', sa.String(length=60), nullable=False),
    sa.Column('bio', sa.String(length=300), nullable=False),
    sa.Column('payout_country', sa.String(length=2), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('terms_version', sa.String(length=32), nullable=False),
    sa.Column('terms_accepted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('payout_status', sa.String(length=16), nullable=False),
    sa.Column('kyc_ref', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id'),
    sa.UniqueConstraint('handle')
    )
    op.create_table('creator_risk_holds',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('creator_id', sa.UUID(), nullable=False),
    sa.Column('reason', sa.String(length=200), nullable=False),
    sa.Column('signals', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('resolved_by', sa.UUID(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.ForeignKeyConstraint(['creator_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_creator_risk_holds_creator_id'), 'creator_risk_holds', ['creator_id'], unique=False)
    op.create_table('settlements',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('creator_id', sa.UUID(), nullable=False),
    sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('amount_micros', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('risk', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('external_ref', sa.String(length=120), nullable=True),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['creator_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_settlements_creator_id'), 'settlements', ['creator_id'], unique=False)
    op.create_table('actor_listings',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('identity_profile_id', sa.UUID(), nullable=False),
    sa.Column('consent_receipt_id', sa.UUID(), nullable=False),
    sa.Column('display_name', sa.String(length=60), nullable=False),
    sa.Column('terms', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('terms_version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('moderation_note', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['identity_profile_id'], ['identity_profiles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_actor_listings_owner_id'), 'actor_listings', ['owner_id'], unique=False)
    op.create_table('settlement_items',
    sa.Column('earning_id', sa.BigInteger(), nullable=False),
    sa.Column('settlement_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['earning_id'], ['creator_earnings.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['settlement_id'], ['settlements.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('earning_id')
    )
    op.create_index(op.f('ix_settlement_items_settlement_id'), 'settlement_items', ['settlement_id'], unique=False)
    op.create_table('actor_licenses',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('listing_id', sa.UUID(), nullable=False),
    sa.Column('licensee_id', sa.UUID(), nullable=False),
    sa.Column('terms_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('terms_version', sa.Integer(), nullable=False),
    sa.Column('price_credits', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('idempotency_key', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['licensee_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['listing_id'], ['actor_listings.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index(op.f('ix_actor_licenses_licensee_id'), 'actor_licenses', ['licensee_id'], unique=False)
    op.create_index(op.f('ix_actor_licenses_listing_id'), 'actor_licenses', ['listing_id'], unique=False)
    op.create_table('license_usage_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('license_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('stage', sa.String(length=16), nullable=False),
    sa.Column('allowed', sa.Boolean(), nullable=False),
    sa.Column('reason', sa.String(length=80), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['license_id'], ['actor_licenses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_license_usage_events_license_id'), 'license_usage_events', ['license_id'], unique=False)
    op.add_column('studio_characters', sa.Column('actor_license_id', sa.UUID(), nullable=True))
    for t in IMMUTABLE:
        op.execute(f"CREATE TRIGGER trg_{t}_immutable BEFORE UPDATE OR DELETE ON {t} "
                   "FOR EACH ROW EXECUTE FUNCTION credit_ledger_immutable()")


def downgrade() -> None:
    for t in IMMUTABLE:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{t}_immutable ON {t}")
    op.drop_column('studio_characters', 'actor_license_id')
    op.drop_index(op.f('ix_license_usage_events_license_id'), table_name='license_usage_events')
    op.drop_table('license_usage_events')
    op.drop_index(op.f('ix_actor_licenses_listing_id'), table_name='actor_licenses')
    op.drop_index(op.f('ix_actor_licenses_licensee_id'), table_name='actor_licenses')
    op.drop_table('actor_licenses')
    op.drop_index(op.f('ix_settlement_items_settlement_id'), table_name='settlement_items')
    op.drop_table('settlement_items')
    op.drop_index(op.f('ix_actor_listings_owner_id'), table_name='actor_listings')
    op.drop_table('actor_listings')
    op.drop_index(op.f('ix_settlements_creator_id'), table_name='settlements')
    op.drop_table('settlements')
    op.drop_index(op.f('ix_creator_risk_holds_creator_id'), table_name='creator_risk_holds')
    op.drop_table('creator_risk_holds')
    op.drop_table('creator_profiles')
    op.drop_index(op.f('ix_creator_earnings_template_id'), table_name='creator_earnings')
    op.drop_index(op.f('ix_creator_earnings_payer_id'), table_name='creator_earnings')
    op.drop_index(op.f('ix_creator_earnings_job_id'), table_name='creator_earnings')
    op.drop_index(op.f('ix_creator_earnings_creator_id'), table_name='creator_earnings')
    op.drop_index(op.f('ix_creator_earnings_created_at'), table_name='creator_earnings')
    op.drop_table('creator_earnings')
    op.drop_table('revenue_policies')
