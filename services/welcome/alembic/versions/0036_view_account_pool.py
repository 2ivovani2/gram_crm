"""GramlyView encrypted MTProto pool and private channel assignments.

Revision ID: 0036
Revises: 0035
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE view_user_state ALTER COLUMN pending_query TYPE text")
    op.execute("""
        CREATE TABLE view_mtproto_account (
            id bigserial PRIMARY KEY,
            telegram_id bigint NOT NULL UNIQUE,
            label varchar(128) NOT NULL DEFAULT '',
            encrypted_session text NOT NULL,
            status varchar(24) NOT NULL DEFAULT 'checking',
            last_error varchar(64) NOT NULL DEFAULT '',
            checked_at timestamptz,
            last_used_at timestamptz,
            last_join_at timestamptz,
            cooldown_until timestamptz,
            disabled boolean NOT NULL DEFAULT false,
            lease_owner varchar(64),
            lease_until timestamptz,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_view_mtproto_pool_health ON view_mtproto_account (disabled, status, checked_at)")
    op.execute("""
        CREATE TABLE view_private_channel (
            id bigserial PRIMARY KEY,
            owner_id bigint NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
            channel_id bigint NOT NULL,
            account_id bigint NOT NULL REFERENCES view_mtproto_account(id),
            invite_ciphertext text NOT NULL,
            invite_fingerprint varchar(64) NOT NULL,
            title varchar(255) NOT NULL,
            status varchar(24) NOT NULL DEFAULT 'active',
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_view_private_owner_channel UNIQUE (owner_id, channel_id)
        )
    """)
    op.execute("CREATE INDEX ix_view_private_channel_account_id ON view_private_channel (account_id)")
    op.execute("CREATE INDEX ix_view_private_invite_fingerprint ON view_private_channel (invite_fingerprint)")
    op.execute("""
        CREATE TABLE view_pool_alert (
            id integer PRIMARY KEY CHECK (id = 1),
            recipient_owner_id bigint REFERENCES owner(id),
            below_threshold boolean NOT NULL DEFAULT true,
            last_notified_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("INSERT INTO view_pool_alert (id) VALUES (1)")


def downgrade() -> None:
    op.drop_table("view_pool_alert")
    op.drop_table("view_private_channel")
    op.drop_table("view_mtproto_account")
    op.execute("UPDATE view_user_state SET pending_query = '' WHERE length(pending_query) > 1024")
    op.execute("ALTER TABLE view_user_state ALTER COLUMN pending_query TYPE varchar(1024)")
