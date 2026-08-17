"""preserve legacy Welcome fields during migration

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE owner ADD COLUMN guide_completed BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE owner ADD COLUMN guide_step INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE owner ADD COLUMN last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()")
    op.execute("ALTER TABLE managed_bot ADD COLUMN webhook_configured BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE channel ADD COLUMN connected_at TIMESTAMPTZ NOT NULL DEFAULT now()")
    op.execute("ALTER TABLE channel ADD COLUMN disconnected_at TIMESTAMPTZ")
    op.execute("ALTER TABLE contact ADD COLUMN gender VARCHAR(16) NOT NULL DEFAULT 'unknown'")
    op.execute("CREATE INDEX ix_contact_gender ON contact (gender)")
    op.execute("ALTER TABLE greeting_delivery ADD COLUMN delay_snapshot_seconds INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE join_request ADD COLUMN delay_snapshot_seconds INTEGER NOT NULL DEFAULT 0")
    op.execute(
        "CREATE UNIQUE INDEX uq_join_open_contact ON join_request (channel_id, contact_id) "
        "WHERE status IN ('pending', 'scheduled', 'processing')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_join_open_contact")
    op.execute("ALTER TABLE join_request DROP COLUMN IF EXISTS delay_snapshot_seconds")
    op.execute("ALTER TABLE greeting_delivery DROP COLUMN IF EXISTS delay_snapshot_seconds")
    op.execute("DROP INDEX IF EXISTS ix_contact_gender")
    op.execute("ALTER TABLE contact DROP COLUMN IF EXISTS gender")
    op.execute("ALTER TABLE channel DROP COLUMN IF EXISTS disconnected_at")
    op.execute("ALTER TABLE channel DROP COLUMN IF EXISTS connected_at")
    op.execute("ALTER TABLE managed_bot DROP COLUMN IF EXISTS webhook_configured")
    op.execute("ALTER TABLE owner DROP COLUMN IF EXISTS last_seen_at")
    op.execute("ALTER TABLE owner DROP COLUMN IF EXISTS guide_step")
    op.execute("ALTER TABLE owner DROP COLUMN IF EXISTS guide_completed")
