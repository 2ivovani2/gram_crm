"""Persist the latest View report selection and opt-in daily monitoring.

Revision ID: 0038
Revises: 0037
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE view_post_observation ADD COLUMN replies_count bigint")
    op.execute("ALTER TABLE view_post_observation ADD COLUMN reactions_count bigint")
    op.execute("ALTER TABLE view_user_state ADD COLUMN report_channel_ids jsonb NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE view_user_state ADD COLUMN report_key varchar(16) NOT NULL DEFAULT ''")
    op.execute("""
        CREATE TABLE view_monitor (
            id bigserial PRIMARY KEY,
            owner_id bigint NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
            channel_id bigint NOT NULL,
            enabled boolean NOT NULL DEFAULT true,
            last_sent_on date,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_view_monitor_owner_channel UNIQUE (owner_id, channel_id)
        )
    """)
    op.execute("CREATE INDEX ix_view_monitor_owner_id ON view_monitor (owner_id)")
    op.execute("CREATE INDEX ix_view_monitor_channel_id ON view_monitor (channel_id)")


def downgrade() -> None:
    op.drop_table("view_monitor")
    op.execute("ALTER TABLE view_user_state DROP COLUMN report_key")
    op.execute("ALTER TABLE view_user_state DROP COLUMN report_channel_ids")
    op.execute("ALTER TABLE view_post_observation DROP COLUMN reactions_count")
    op.execute("ALTER TABLE view_post_observation DROP COLUMN replies_count")
