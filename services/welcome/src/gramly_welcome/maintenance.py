from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text

from .config import get_settings
from .db import session_factory
from .models import (
    EventLog,
    IdempotencyRecord,
    InboxEvent,
    InboxSource,
    QueueStatus,
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


def run() -> None:
    asyncio.run(maintain())


if __name__ == "__main__":
    run()
