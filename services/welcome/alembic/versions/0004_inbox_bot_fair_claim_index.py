"""drive fair inbox claims by managed bot id

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_inbox_fair_pending")
    op.execute(
        """
        CREATE INDEX ix_inbox_fair_pending
        ON inbox_event(bot_id, available_at, id)
        WHERE status IN ('pending', 'retry')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_inbox_fair_pending")
    op.execute(
        """
        CREATE INDEX ix_inbox_fair_pending
        ON inbox_event(source_key, available_at, id)
        WHERE status IN ('pending', 'retry')
        """
    )
