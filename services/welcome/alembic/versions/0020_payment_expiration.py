"""add durable payment expiration

Revision ID: 0020
Revises: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE payment
          ADD COLUMN expires_at TIMESTAMPTZ,
          ADD COLUMN reconcile_after TIMESTAMPTZ,
          ADD COLUMN reconcile_attempts INTEGER NOT NULL DEFAULT 0;

        UPDATE payment
        SET expires_at = created_at + INTERVAL '15 minutes',
            reconcile_after = created_at + INTERVAL '15 minutes';

        ALTER TABLE payment ALTER COLUMN expires_at SET NOT NULL;
        ALTER TABLE payment ALTER COLUMN expires_at SET DEFAULT (now() + INTERVAL '15 minutes');
        CREATE INDEX ix_payment_expiration
          ON payment (status, reconcile_after, expires_at, id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS ix_payment_expiration;
        ALTER TABLE payment
          DROP COLUMN reconcile_attempts,
          DROP COLUMN reconcile_after,
          DROP COLUMN expires_at;
        """
    )
