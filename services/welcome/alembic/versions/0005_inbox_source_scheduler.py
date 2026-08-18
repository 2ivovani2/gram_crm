"""add durable round-robin inbox source scheduler

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE inbox_source (
          source_key VARCHAR(96) PRIMARY KEY,
          bot_id BIGINT REFERENCES managed_bot(id) ON DELETE CASCADE,
          last_claimed_at TIMESTAMPTZ NOT NULL
            DEFAULT '1970-01-01 00:00:00+00'::timestamptz,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_inbox_source_bot_id ON inbox_source(bot_id)")
    op.execute(
        "CREATE INDEX ix_inbox_source_claim_order "
        "ON inbox_source(last_claimed_at, source_key)"
    )
    op.execute(
        """
        INSERT INTO inbox_source(source_key, bot_id)
        SELECT source_key, min(bot_id)
        FROM inbox_event
        GROUP BY source_key
        ON CONFLICT (source_key) DO NOTHING
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_inbox_fair_pending")
    op.execute(
        """
        CREATE INDEX ix_inbox_fair_pending
        ON inbox_event(source_key, available_at, id)
        WHERE status IN ('pending', 'retry')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_inbox_fair_pending")
    op.execute(
        """
        CREATE INDEX ix_inbox_fair_pending
        ON inbox_event(bot_id, available_at, id)
        WHERE status IN ('pending', 'retry')
        """
    )
    op.execute("DROP TABLE IF EXISTS inbox_source")
