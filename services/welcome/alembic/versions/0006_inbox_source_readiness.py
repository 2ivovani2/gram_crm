"""track ready inbox sources without scanning completed events

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE inbox_source "
        "ADD COLUMN pending_count BIGINT NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE inbox_source "
        "ADD COLUMN next_available_at TIMESTAMPTZ"
    )
    op.execute(
        """
        UPDATE inbox_source AS source
        SET pending_count = queue.pending_count,
            next_available_at = queue.next_available_at
        FROM (
          SELECT source.source_key,
                 count(event.id) AS pending_count,
                 min(event.available_at) AS next_available_at
          FROM inbox_source AS source
          LEFT JOIN inbox_event AS event
            ON event.source_key = source.source_key
           AND event.status IN ('pending', 'retry')
          GROUP BY source.source_key
        ) AS queue
        WHERE queue.source_key = source.source_key
        """
    )
    op.execute(
        "ALTER TABLE inbox_source ADD CONSTRAINT ck_inbox_source_pending_count "
        "CHECK (pending_count >= 0)"
    )
    # The scheduler table is intentionally tiny. A sequential scan and sort is
    # cheaper and avoids index churn on every round-robin rotation.
    op.execute("DROP INDEX IF EXISTS ix_inbox_source_claim_order")
    # Keep one partial index for ready events and one for expired leases. The
    # generic status/claim indexes amplified every inbox state transition.
    op.execute("DROP INDEX IF EXISTS ix_inbox_claim")
    op.execute("DROP INDEX IF EXISTS ix_inbox_event_status")
    op.execute(
        """
        CREATE INDEX ix_inbox_expired_lease
        ON inbox_event(lease_expires_at, available_at, id)
        WHERE status = 'processing'
        """
    )
    op.execute(
        "CREATE INDEX ix_inbox_dead ON inbox_event(id) WHERE status = 'dead'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_inbox_dead")
    op.execute("DROP INDEX IF EXISTS ix_inbox_expired_lease")
    op.execute("CREATE INDEX ix_inbox_event_status ON inbox_event(status)")
    op.execute(
        "CREATE INDEX ix_inbox_claim "
        "ON inbox_event(status, available_at, lease_expires_at)"
    )
    op.execute(
        "CREATE INDEX ix_inbox_source_claim_order "
        "ON inbox_source(last_claimed_at, source_key)"
    )
    op.execute(
        "ALTER TABLE inbox_source DROP CONSTRAINT IF EXISTS "
        "ck_inbox_source_pending_count"
    )
    op.execute("ALTER TABLE inbox_source DROP COLUMN IF EXISTS next_available_at")
    op.execute("ALTER TABLE inbox_source DROP COLUMN IF EXISTS pending_count")
