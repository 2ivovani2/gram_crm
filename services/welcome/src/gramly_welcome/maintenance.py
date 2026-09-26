from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text, update

from .business_metrics import ensure_weekly_snapshot
from .config import get_settings
from .db import session_factory
from .learning import purge_expired_tip_sessions
from .models import (
    EventLog,
    IdempotencyRecord,
    InboxEvent,
    InboxSource,
    JoinRequestActivation,
    QueueStatus,
    ViewChannelSnapshot,
    ViewCollectionRun,
    ViewPostObservation,
    WebSession,
)


async def maintain() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    async with session_factory() as session:
        async with session.begin():
            # Lock the tiny scheduler table so webhook increments and worker
            # decrements cannot race this defensive reconciliation.
            await session.execute(select(InboxSource.source_key).with_for_update())
            await session.execute(
                text(
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
            )
            await session.execute(
                delete(InboxEvent).where(
                    InboxEvent.status == QueueStatus.COMPLETED.value,
                    InboxEvent.received_at < now - timedelta(days=settings.raw_event_retention_days),
                )
            )
            await session.execute(
                delete(InboxEvent).where(
                    InboxEvent.status == QueueStatus.DEAD.value,
                    InboxEvent.received_at < now - timedelta(days=settings.technical_retention_days),
                )
            )
            await session.execute(
                delete(EventLog).where(
                    EventLog.created_at < now - timedelta(days=settings.technical_retention_days)
                )
            )
            await session.execute(delete(WebSession).where(WebSession.expires_at < now))
            await session.execute(delete(IdempotencyRecord).where(IdempotencyRecord.expires_at < now))
            await session.execute(
                update(JoinRequestActivation)
                .where(
                    JoinRequestActivation.status.in_(("sent", "approved_without_activation")),
                    JoinRequestActivation.expires_at < now,
                )
                .values(status="expired", error="activation_expired")
            )
            # Keep the active scheduler compact. Partitions are created before
            # copying so dropping old history is a metadata-only operation.
            await session.execute(
                text(
                    """
                    DO $$
                    DECLARE previous_month date := (date_trunc('month', now()) - interval '1 month')::date;
                    DECLARE start_month date := date_trunc('month', now())::date;
                    DECLARE next_month date := (start_month + interval '1 month')::date;
                    DECLARE after_next date := (start_month + interval '2 months')::date;
                    DECLARE expired_partition text :=
                      'delivery_job_history_' || to_char(start_month - interval '7 months', 'YYYYMM');
                    BEGIN
                      EXECUTE format(
                        'CREATE TABLE IF NOT EXISTS %I PARTITION OF delivery_job_history '
                        'FOR VALUES FROM (%L) TO (%L)',
                        'delivery_job_history_' || to_char(previous_month, 'YYYYMM'),
                        previous_month, start_month
                      );
                      EXECUTE format(
                        'CREATE TABLE IF NOT EXISTS %I PARTITION OF delivery_job_history '
                        'FOR VALUES FROM (%L) TO (%L)',
                        'delivery_job_history_' || to_char(start_month, 'YYYYMM'),
                        start_month, next_month
                      );
                      EXECUTE format(
                        'CREATE TABLE IF NOT EXISTS %I PARTITION OF delivery_job_history '
                        'FOR VALUES FROM (%L) TO (%L)',
                        'delivery_job_history_' || to_char(next_month, 'YYYYMM'),
                        next_month, after_next
                      );
                      EXECUTE format('DROP TABLE IF EXISTS %I', expired_partition);
                    END $$
                    """
                )
            )
            await session.execute(
                text(
                    """
                    WITH archived AS (
                      DELETE FROM delivery_job
                      WHERE id IN (
                        SELECT id FROM delivery_job
                        WHERE status IN ('completed','failed','cancelled')
                          AND updated_at < now() - interval '7 days'
                        ORDER BY id
                        LIMIT 10000
                        FOR UPDATE SKIP LOCKED
                      )
                      RETURNING *
                    )
                    INSERT INTO delivery_job_history
                      (id, source_kind, source_id, bot_id, lane, status, due_at,
                       deadline_at, attempts, error_code, created_at, finished_at)
                    SELECT id, source_kind, source_id, bot_id, lane, status, due_at,
                           deadline_at, attempts, error_code, created_at, updated_at
                    FROM archived
                    ON CONFLICT DO NOTHING
                    """
                )
            )
    # Bound analytics retention even if the optional collector is suspended.
    for model, date_column, days in (
        (ViewPostObservation, ViewPostObservation.observed_at, 7),
        (ViewCollectionRun, ViewCollectionRun.observed_at, 14),
        (ViewChannelSnapshot, ViewChannelSnapshot.observed_at, 90),
    ):
        for _ in range(5):
            async with session_factory() as session, session.begin():
                ids = select(model.id).where(date_column < now - timedelta(days=days)).order_by(model.id).limit(10_000)
                result = await session.execute(delete(model).where(model.id.in_(ids)))
                if not getattr(result, "rowcount", 0):
                    break
    async with session_factory() as session:
        await purge_expired_tip_sessions(session, now=now)
    async with session_factory() as session:
        await ensure_weekly_snapshot(session, now=now)


def run() -> None:
    asyncio.run(maintain())


if __name__ == "__main__":
    run()
