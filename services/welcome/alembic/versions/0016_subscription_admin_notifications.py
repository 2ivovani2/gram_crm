"""Allow durable owner notifications for subscription changes.

Revision ID: 0016
Revises: 0015
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_owner_notification_kind", "owner_notification", type_="check")
    op.create_check_constraint(
        "ck_owner_notification_kind",
        "owner_notification",
        "kind IN ('onboarding','announcement','subscription_change')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM owner_notification WHERE kind = 'subscription_change'")
    op.drop_constraint("ck_owner_notification_kind", "owner_notification", type_="check")
    op.create_check_constraint(
        "ck_owner_notification_kind",
        "owner_notification",
        "kind IN ('onboarding','announcement')",
    )
