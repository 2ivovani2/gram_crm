"""Bounded client bot diagnostics and deduplicated incident notifications."""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE managed_bot ADD COLUMN delivery_issues JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE owner_notification DROP CONSTRAINT ck_owner_notification_kind")
    op.execute("""ALTER TABLE owner_notification ADD CONSTRAINT ck_owner_notification_kind
        CHECK (kind IN ('onboarding','announcement','subscription_change','delivery_incident'))""")


def downgrade() -> None:
    raise RuntimeError("Keep additive incident history during image rollback")
