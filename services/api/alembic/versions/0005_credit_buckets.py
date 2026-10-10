"""credit buckets (lots + allocations), reserve/settle/release billing state

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-10
Additive. Existing balances become one `legacy` lot per user (no expiry), so balances are unchanged.
New ledger_reason values cannot be removed on downgrade (PostgreSQL); they are harmless when unused.
"""
import sqlalchemy as sa

from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE ledger_reason ADD VALUE IF NOT EXISTS 'generation_settle'")
    op.execute("ALTER TYPE ledger_reason ADD VALUE IF NOT EXISTS 'expire'")
    op.create_table(
        'credit_lots',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('bucket', sa.String(20), nullable=False),
        sa.Column('source_ledger_id', sa.BigInteger(), nullable=False),
        sa.Column('granted', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('granted > 0', name='ck_credit_lots_granted_pos'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['source_ledger_id'], ['credit_ledger.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_ledger_id'),
    )
    op.create_index('ix_credit_lots_user_id', 'credit_lots', ['user_id'])
    op.create_table(
        'credit_allocations',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('ledger_id', sa.BigInteger(), nullable=False),
        sa.Column('lot_id', sa.UUID(), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.CheckConstraint('amount <> 0', name='ck_credit_allocations_nonzero'),
        sa.ForeignKeyConstraint(['ledger_id'], ['credit_ledger.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['lot_id'], ['credit_lots.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_credit_allocations_ledger_id', 'credit_allocations', ['ledger_id'])
    op.create_index('ix_credit_allocations_lot_id', 'credit_allocations', ['lot_id'])
    for t in ('credit_lots', 'credit_allocations'):
        op.execute(f"""
        CREATE TRIGGER trg_{t}_immutable BEFORE UPDATE OR DELETE ON {t}
        FOR EACH ROW EXECUTE FUNCTION credit_ledger_immutable();
        """)
    # Existing balances -> one legacy lot per user, sourced from the user's latest ledger row.
    op.execute("""
    INSERT INTO credit_lots (id, user_id, bucket, source_ledger_id, granted, expires_at)
    SELECT gen_random_uuid(), l.user_id, 'legacy', l.id, l.balance_after, NULL
    FROM credit_ledger l
    JOIN (SELECT user_id, max(id) AS id FROM credit_ledger GROUP BY user_id) last ON last.id = l.id
    WHERE l.balance_after > 0
    """)
    op.add_column('generation_jobs', sa.Column('billing_state', sa.String(12), nullable=True))
    op.add_column('generation_jobs', sa.Column('est_cost_usd', sa.Float(), nullable=True))
    # Jobs that already hold a debit and are still running are reservations awaiting settle/release.
    op.execute("""
    UPDATE generation_jobs SET billing_state = CASE
      WHEN credit_cost = 0 THEN NULL
      WHEN refunded THEN 'released'
      WHEN status = 'completed' THEN 'settled'
      ELSE 'reserved' END
    """)


def downgrade() -> None:
    op.drop_column('generation_jobs', 'est_cost_usd')
    op.drop_column('generation_jobs', 'billing_state')
    for t in ('credit_allocations', 'credit_lots'):
        op.execute(f'DROP TRIGGER IF EXISTS trg_{t}_immutable ON {t}')
    op.drop_index('ix_credit_allocations_lot_id', table_name='credit_allocations')
    op.drop_index('ix_credit_allocations_ledger_id', table_name='credit_allocations')
    op.drop_table('credit_allocations')
    op.drop_index('ix_credit_lots_user_id', table_name='credit_lots')
    op.drop_table('credit_lots')
