from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from .config import get_settings
from .db import session_factory
from .models import EventLog, InboxEvent, QueueStatus


async def maintain() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    async with session_factory() as session:
        async with session.begin():
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


def run() -> None:
    asyncio.run(maintain())


if __name__ == "__main__":
    run()
