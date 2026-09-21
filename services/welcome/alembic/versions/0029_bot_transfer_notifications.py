"""Allow durable owner notifications for administrative bot transfers."""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE owner_notification DROP CONSTRAINT ck_owner_notification_kind")
    op.execute(
        """ALTER TABLE owner_notification ADD CONSTRAINT ck_owner_notification_kind
        CHECK (kind IN ('onboarding','announcement','subscription_change',
                        'delivery_incident','bot_transfer'))"""
    )


def downgrade() -> None:
    op.execute("DELETE FROM owner_notification WHERE kind = 'bot_transfer'")
    op.execute("ALTER TABLE owner_notification DROP CONSTRAINT ck_owner_notification_kind")
    op.execute(
        """ALTER TABLE owner_notification ADD CONSTRAINT ck_owner_notification_kind
        CHECK (kind IN ('onboarding','announcement','subscription_change','delivery_incident'))"""
    )
