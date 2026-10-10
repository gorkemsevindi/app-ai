"""templates v3: person slots, visibility/moderation, rights, source clip, aggregated metrics

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-10
Additive only: server defaults keep every existing template public + approved, so current clients and
single-identity jobs behave exactly as before.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # Pre-existing drift fix: 0001 declared fk_templates_current_version with use_alter=True inside
    # create_table, which Alembic does not emit, so the FK never existed. NOT VALID avoids failing on any
    # legacy inconsistent rows; run `ALTER TABLE templates VALIDATE CONSTRAINT fk_templates_current_version`
    # after checking production data.
    op.execute("ALTER TABLE templates ADD CONSTRAINT fk_templates_current_version FOREIGN KEY "
               "(current_version_id) REFERENCES template_versions (id) NOT VALID")
    op.add_column('templates', sa.Column('creator_id', sa.UUID(), nullable=True))
    op.create_foreign_key('fk_templates_creator', 'templates', 'users', ['creator_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_templates_creator_id', 'templates', ['creator_id'])
    op.add_column('templates', sa.Column('visibility', sa.String(16), server_default='public', nullable=False))
    op.add_column('templates', sa.Column('moderation_status', sa.String(16), server_default='approved',
                                         nullable=False))
    op.add_column('templates', sa.Column('commercial_rights', JSONB, server_default='{}', nullable=False))
    op.create_check_constraint('ck_templates_visibility', 'templates',
                               "visibility in ('draft','private','unlisted','public','blocked')")
    op.create_check_constraint('ck_templates_moderation', 'templates',
                               "moderation_status in ('pending','approved','rejected','review')")

    op.add_column('template_versions', sa.Column('source_video_id', sa.UUID(), nullable=True))
    op.create_foreign_key('fk_template_versions_source_video', 'template_versions', 'source_videos',
                          ['source_video_id'], ['id'], ondelete='SET NULL')
    op.add_column('template_versions', sa.Column('config', JSONB, server_default='{}', nullable=False))
    op.add_column('template_versions', sa.Column('credit_rule', JSONB, server_default='{}', nullable=False))
    op.add_column('template_versions', sa.Column('published_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'template_person_slots',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('template_version_id', sa.UUID(), nullable=False),
        sa.Column('slot_id', sa.String(32), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(60), nullable=False),
        sa.Column('track_id', sa.Integer(), nullable=False),
        sa.Column('required', sa.Boolean(), nullable=False),
        sa.Column('requirements', JSONB, nullable=False),
        sa.ForeignKeyConstraint(['template_version_id'], ['template_versions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('template_version_id', 'slot_id', name='uq_template_slots_slot'),
        sa.UniqueConstraint('template_version_id', 'track_id', name='uq_template_slots_track'),
    )
    op.create_index('ix_template_person_slots_template_version_id', 'template_person_slots',
                    ['template_version_id'])
    op.create_table(
        'template_metrics_daily',
        sa.Column('template_id', sa.UUID(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        *[sa.Column(c, sa.Integer(), nullable=False) for c in
          ('opens', 'shares', 'generations', 'successes', 'failures', 'paid_uses', 'reports', 'credits_charged')],
        sa.Column('cost_usd', sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(['template_id'], ['templates.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('template_id', 'day'),
    )
    # Published template versions are immutable recipes: block edits to the fields that define an output.
    op.execute("""
    CREATE OR REPLACE FUNCTION template_version_immutable() RETURNS trigger AS $$
    BEGIN
      IF OLD.published_at IS NOT NULL AND (NEW.prompt_recipe IS DISTINCT FROM OLD.prompt_recipe
         OR NEW.source_video_id IS DISTINCT FROM OLD.source_video_id OR NEW.config IS DISTINCT FROM OLD.config
         OR NEW.credit_rule IS DISTINCT FROM OLD.credit_rule OR NEW.params IS DISTINCT FROM OLD.params) THEN
        RAISE EXCEPTION 'published template versions are immutable';
      END IF;
      RETURN NEW;
    END $$ LANGUAGE plpgsql;
    CREATE TRIGGER trg_template_version_immutable BEFORE UPDATE ON template_versions
      FOR EACH ROW EXECUTE FUNCTION template_version_immutable();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_template_version_immutable ON template_versions;"
               "DROP FUNCTION IF EXISTS template_version_immutable();")
    op.drop_table('template_metrics_daily')
    op.drop_index('ix_template_person_slots_template_version_id', table_name='template_person_slots')
    op.drop_table('template_person_slots')
    op.drop_column('template_versions', 'published_at')
    op.drop_column('template_versions', 'credit_rule')
    op.drop_column('template_versions', 'config')
    op.drop_constraint('fk_template_versions_source_video', 'template_versions', type_='foreignkey')
    op.drop_column('template_versions', 'source_video_id')
    op.drop_constraint('ck_templates_moderation', 'templates', type_='check')
    op.drop_constraint('ck_templates_visibility', 'templates', type_='check')
    op.drop_column('templates', 'commercial_rights')
    op.drop_column('templates', 'moderation_status')
    op.drop_column('templates', 'visibility')
    op.drop_index('ix_templates_creator_id', table_name='templates')
    op.drop_constraint('fk_templates_creator', 'templates', type_='foreignkey')
    op.drop_column('templates', 'creator_id')
    op.execute("ALTER TABLE templates DROP CONSTRAINT IF EXISTS fk_templates_current_version")
