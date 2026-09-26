"""Fan out GramlyView pool alerts without losing the existing recipient.

Revision ID: 0037
Revises: 0036
"""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE view_pool_alert_recipient (
            owner_id bigint PRIMARY KEY REFERENCES owner(id) ON DELETE CASCADE,
            last_notified_event_at timestamptz,
            claim_until timestamptz,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        INSERT INTO view_pool_alert_recipient (owner_id, last_notified_event_at)
        SELECT recipient_owner_id, last_notified_at
        FROM view_pool_alert WHERE recipient_owner_id IS NOT NULL
        ON CONFLICT (owner_id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("view_pool_alert_recipient")
