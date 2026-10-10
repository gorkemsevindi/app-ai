"""share links, referral clicks, attributions (V4 Stage A3)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-10
Additive only.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'share_links',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('code', sa.String(32), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('template_id', sa.UUID(), nullable=False),
        sa.Column('job_id', sa.UUID(), nullable=True),
        sa.Column('campaign', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['template_id'], ['templates.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['job_id'], ['generation_jobs.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_index('ix_share_links_owner_id', 'share_links', ['owner_id'])
    op.create_index('ix_share_links_template_id', 'share_links', ['template_id'])
    op.create_table(
        'referral_clicks',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('link_id', sa.UUID(), nullable=False),
        sa.Column('source', sa.String(10), nullable=False),
        sa.Column('ip_hash', sa.String(64), nullable=False),
        sa.Column('platform', sa.String(10), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['link_id'], ['share_links.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_referral_clicks_link_id', 'referral_clicks', ['link_id'])
    op.create_index('ix_referral_clicks_created_at', 'referral_clicks', ['created_at'])
    op.create_table(
        'attributions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('link_id', sa.UUID(), nullable=True),
        sa.Column('click_id', sa.BigInteger(), nullable=True),
        sa.Column('referrer_id', sa.UUID(), nullable=True),
        sa.Column('template_id', sa.UUID(), nullable=True),
        sa.Column('campaign', sa.String(64), nullable=True),
        sa.Column('model', sa.String(20), nullable=False),
        sa.Column('policy', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('attributed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['link_id'], ['share_links.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['click_id'], ['referral_clicks.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['referrer_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['template_id'], ['templates.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )
    op.create_index('ix_attributions_referrer_id', 'attributions', ['referrer_id'])


def downgrade() -> None:
    op.drop_index('ix_attributions_referrer_id', table_name='attributions')
    op.drop_table('attributions')
    op.drop_index('ix_referral_clicks_created_at', table_name='referral_clicks')
    op.drop_index('ix_referral_clicks_link_id', table_name='referral_clicks')
    op.drop_table('referral_clicks')
    op.drop_index('ix_share_links_template_id', table_name='share_links')
    op.drop_index('ix_share_links_owner_id', table_name='share_links')
    op.drop_table('share_links')
