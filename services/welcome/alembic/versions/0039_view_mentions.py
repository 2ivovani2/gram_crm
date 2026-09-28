"""Opt-in mentions within indexed public GramlyView channels.

Revision ID: 0039
Revises: 0038
"""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE view_mention_monitor (
            id bigserial PRIMARY KEY,
            owner_id bigint NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
            channel_id bigint NOT NULL,
            username varchar(64) NOT NULL,
            enabled boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_view_mention_monitor_owner_channel UNIQUE (owner_id, channel_id)
        )
    """)
    op.execute("CREATE INDEX ix_view_mention_monitor_owner_id ON view_mention_monitor (owner_id)")
    op.execute("CREATE INDEX ix_view_mention_monitor_channel_id ON view_mention_monitor (channel_id)")
    op.execute("CREATE INDEX ix_view_mention_monitor_active ON view_mention_monitor (lower(username)) WHERE enabled")
    op.execute("""
        CREATE TABLE view_mention_event (
            id bigserial PRIMARY KEY,
            monitor_id bigint NOT NULL REFERENCES view_mention_monitor(id) ON DELETE CASCADE,
            source_channel_id bigint NOT NULL,
            source_username varchar(64) NOT NULL,
            post_id bigint NOT NULL,
            published_at timestamptz NOT NULL,
            observed_at timestamptz NOT NULL DEFAULT now(),
            notified_at timestamptz,
            claim_until timestamptz,
            attempts integer NOT NULL DEFAULT 0,
            CONSTRAINT uq_view_mention_event_post UNIQUE (monitor_id, source_channel_id, post_id)
        )
    """)
    op.execute("CREATE INDEX ix_view_mention_event_monitor_id ON view_mention_event (monitor_id)")
    op.execute("CREATE INDEX ix_view_mention_event_notified_at ON view_mention_event (notified_at)")
    op.execute("CREATE INDEX ix_view_mention_event_pending ON view_mention_event (id) WHERE notified_at IS NULL")


def downgrade() -> None:
    op.drop_table("view_mention_event")
    op.drop_table("view_mention_monitor")
