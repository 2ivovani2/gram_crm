"""Shared service activity and first-party View observations.

Revision ID: 0035
Revises: 0034
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE owner_service_activity (
            id bigserial PRIMARY KEY,
            owner_id bigint NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
            service varchar(16) NOT NULL CHECK (service IN ('hello', 'view')),
            first_seen_at timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            last_event varchar(48) NOT NULL DEFAULT 'start',
            cross_offer_hidden boolean NOT NULL DEFAULT false,
            cross_offer_at timestamptz,
            CONSTRAINT uq_owner_service_activity UNIQUE (owner_id, service)
        )
    """)
    op.execute("CREATE INDEX ix_owner_service_activity_owner_id ON owner_service_activity (owner_id)")
    op.execute("""
        INSERT INTO owner_service_activity (owner_id, service, first_seen_at, last_seen_at, last_event)
        SELECT id, 'hello', created_at, last_seen_at, 'legacy_hello' FROM owner
    """)
    op.execute("""
        CREATE TABLE view_channel_snapshot (
            id bigserial PRIMARY KEY,
            channel_id bigint NOT NULL,
            username varchar(64) NOT NULL DEFAULT '',
            title varchar(255) NOT NULL,
            subscriber_count bigint,
            observed_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_view_channel_snapshot_channel_id ON view_channel_snapshot (channel_id)")
    op.execute("CREATE INDEX ix_view_channel_snapshot_observed_at ON view_channel_snapshot (observed_at)")
    op.execute("CREATE INDEX ix_view_channel_snapshot_username_fresh ON view_channel_snapshot (lower(username), observed_at DESC)")
    op.execute("""
        CREATE TABLE view_post_observation (
            id bigserial PRIMARY KEY,
            channel_id bigint NOT NULL,
            post_id bigint NOT NULL,
            published_at timestamptz NOT NULL,
            observed_at timestamptz NOT NULL,
            views bigint NOT NULL,
            forwards bigint,
            source varchar(24) NOT NULL DEFAULT 'mtproto',
            CONSTRAINT uq_view_post_observation UNIQUE (channel_id, post_id, observed_at)
        )
    """)
    op.execute("CREATE INDEX ix_view_post_observation_channel_id ON view_post_observation (channel_id)")
    op.execute("CREATE INDEX ix_view_post_observation_published_at ON view_post_observation (published_at)")
    op.execute("CREATE INDEX ix_view_post_observation_observed_at ON view_post_observation (observed_at)")
    op.execute("CREATE INDEX ix_view_post_observation_channel_run ON view_post_observation (channel_id, observed_at, published_at)")
    op.execute("""
        CREATE TABLE view_collection_run (
            id bigserial PRIMARY KEY,
            channel_id bigint NOT NULL,
            observed_at timestamptz NOT NULL,
            coverage_start_at timestamptz NOT NULL,
            complete boolean NOT NULL DEFAULT false,
            post_count integer NOT NULL DEFAULT 0,
            error_code varchar(48) NOT NULL DEFAULT ''
        )
    """)
    op.execute("CREATE INDEX ix_view_collection_run_channel_id ON view_collection_run (channel_id)")
    op.execute("CREATE INDEX ix_view_collection_run_observed_at ON view_collection_run (observed_at)")
    op.execute("CREATE INDEX ix_view_collection_run_channel_latest ON view_collection_run (channel_id, observed_at DESC)")
    op.execute("""
        CREATE TABLE view_favorite (
            id bigserial PRIMARY KEY,
            owner_id bigint NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
            channel_id bigint NOT NULL,
            username varchar(64) NOT NULL DEFAULT '',
            title varchar(255) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_view_favorite_owner_channel UNIQUE (owner_id, channel_id)
        )
    """)
    op.execute("CREATE INDEX ix_view_favorite_owner_id ON view_favorite (owner_id)")
    op.execute("""
        CREATE TABLE view_collection (
            id bigserial PRIMARY KEY,
            owner_id bigint NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
            name varchar(80) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_view_collection_owner_name UNIQUE (owner_id, name)
        )
    """)
    op.execute("CREATE INDEX ix_view_collection_owner_id ON view_collection (owner_id)")
    op.execute("""
        CREATE TABLE view_collection_item (
            id bigserial PRIMARY KEY,
            collection_id bigint NOT NULL REFERENCES view_collection(id) ON DELETE CASCADE,
            channel_id bigint NOT NULL,
            username varchar(64) NOT NULL,
            title varchar(255) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_view_collection_item UNIQUE (collection_id, channel_id)
        )
    """)
    op.execute("CREATE INDEX ix_view_collection_item_collection_id ON view_collection_item (collection_id)")
    op.execute("""
        CREATE TABLE view_user_state (
            owner_id bigint PRIMARY KEY REFERENCES owner(id) ON DELETE CASCADE,
            pending_query varchar(1024) NOT NULL DEFAULT '',
            pending_action varchar(64) NOT NULL DEFAULT '',
            last_analysis_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.drop_table("view_user_state")
    op.drop_table("view_collection_item")
    op.drop_table("view_collection")
    op.drop_table("view_favorite")
    op.drop_table("view_collection_run")
    op.drop_table("view_post_observation")
    op.drop_table("view_channel_snapshot")
    op.drop_table("owner_service_activity")
