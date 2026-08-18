"""index the bounded per-source inbox claim path

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX ix_inbox_fair_pending
        ON inbox_event(source_key, available_at, id)
        WHERE status IN ('pending', 'retry')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_inbox_fair_pending")
