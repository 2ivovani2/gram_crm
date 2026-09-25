"""Indexes for opt-in audience, refunds, and expired greeting windows.

Revision ID: 0034
Revises: 0033
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_contact_active_consent_dashboard "
            "ON contact (consented_at, bot_id, telegram_id) "
            "WHERE consent_status = 'active' AND bot_started AND delivery_status <> 'dead'"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payment_refunded_at "
            "ON payment (refunded_at) WHERE status = 'refunded'"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_delivery_operation_expired_window "
            "ON delivery_operation (flow_delivery_id) WHERE error = 'join_request_window_expired'"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_subscriber_challenge_passed_at "
            "ON subscriber_challenge (completed_at, contact_id) WHERE status = 'passed'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_subscriber_challenge_passed_at")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_delivery_operation_expired_window")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_payment_refunded_at")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_contact_active_consent_dashboard")
