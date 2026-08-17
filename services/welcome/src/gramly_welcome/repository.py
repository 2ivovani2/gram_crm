from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from .models import GreetingDelivery, InboxEvent, ManagedBot, QueueStatus


@dataclass(frozen=True)
class BotWebhookIdentity:
    id: int
    public_id: uuid.UUID
    path_secret: str
    webhook_secret: str


async def find_active_bot(session: AsyncSession, public_id: uuid.UUID) -> BotWebhookIdentity | None:
    row = (
        await session.execute(
            select(
                ManagedBot.id,
                ManagedBot.public_id,
                ManagedBot.path_secret,
                ManagedBot.webhook_secret,
            ).where(ManagedBot.public_id == public_id, ManagedBot.is_active.is_(True))
        )
    ).one_or_none()
    return BotWebhookIdentity(*row) if row else None


async def insert_inbox_event(
    session: AsyncSession,
    *,
    source_key: str,
    update_id: int,
    payload: dict[str, Any],
    bot_id: int | None,
) -> bool:
    statement = (
        insert(InboxEvent)
        .values(source_key=source_key, update_id=update_id, payload=payload, bot_id=bot_id)
        .on_conflict_do_nothing(constraint="uq_inbox_source_update")
        .returning(InboxEvent.id)
    )
    return (await session.scalar(statement)) is not None


def _inbox_claim_query(now: datetime) -> Select[tuple[InboxEvent]]:
    return (
        select(InboxEvent)
        .where(
            or_(
                and_(
                    InboxEvent.status.in_((QueueStatus.PENDING.value, QueueStatus.RETRY.value)),
                    InboxEvent.available_at <= now,
                ),
                and_(
                    InboxEvent.status == QueueStatus.PROCESSING.value,
                    InboxEvent.lease_expires_at < now,
                ),
            )
        )
        .order_by(InboxEvent.available_at, InboxEvent.id)
        .with_for_update(skip_locked=True)
    )


async def claim_inbox_batch(
    session: AsyncSession, *, worker_id: str, limit: int, lease_seconds: int
) -> list[InboxEvent]:
    now = datetime.now(UTC)
    async with session.begin():
        events = list((await session.scalars(_inbox_claim_query(now).limit(limit))).all())
        lease_until = now + timedelta(seconds=lease_seconds)
        for event in events:
            event.status = QueueStatus.PROCESSING.value
            event.lease_owner = worker_id
            event.lease_expires_at = lease_until
            event.attempts += 1
    return events


async def finish_inbox_event(session: AsyncSession, event_id: int, worker_id: str) -> bool:
    result = await session.execute(
        update(InboxEvent)
        .where(
            InboxEvent.id == event_id,
            InboxEvent.status == QueueStatus.PROCESSING.value,
            InboxEvent.lease_owner == worker_id,
        )
        .values(
            status=QueueStatus.COMPLETED.value,
            processed_at=datetime.now(UTC),
            lease_owner=None,
            lease_expires_at=None,
            last_error="",
        )
    )
    await session.commit()
    return bool(cast(CursorResult[tuple[int]], result).rowcount)


async def retry_inbox_event(
    session: AsyncSession,
    event: InboxEvent,
    worker_id: str,
    error: str,
    max_attempts: int,
) -> None:
    dead = event.attempts >= max_attempts
    base_delay = min(300, 2 ** min(event.attempts, 8))
    delay = base_delay + secrets.randbelow(max(1, base_delay // 2 + 1))
    await session.execute(
        update(InboxEvent)
        .where(InboxEvent.id == event.id, InboxEvent.lease_owner == worker_id)
        .values(
            status=QueueStatus.DEAD.value if dead else QueueStatus.RETRY.value,
            available_at=datetime.now(UTC) + timedelta(seconds=delay),
            lease_owner=None,
            lease_expires_at=None,
            last_error=error[:500],
        )
    )
    await session.commit()


async def claim_delivery_batch(
    session: AsyncSession, *, worker_id: str, limit: int, lease_seconds: int
) -> list[GreetingDelivery]:
    now = datetime.now(UTC)
    async with session.begin():
        # At most one delivery per bot per claim prevents a noisy customer from
        # monopolising a worker batch. Other workers can still claim other bots.
        ranked = (
            select(
                GreetingDelivery.id,
                GreetingDelivery.bot_id,
            )
            .where(
                or_(
                    and_(
                        GreetingDelivery.status.in_(("scheduled", "retry")),
                        GreetingDelivery.due_at <= now,
                    ),
                    and_(
                        GreetingDelivery.status == "processing",
                        GreetingDelivery.lease_expires_at < now,
                    ),
                )
            )
            .distinct(GreetingDelivery.bot_id)
            .order_by(GreetingDelivery.bot_id, GreetingDelivery.due_at, GreetingDelivery.id)
            .limit(limit)
            .subquery()
        )
        deliveries = list(
            (
                await session.scalars(
                    select(GreetingDelivery)
                    .join(ranked, ranked.c.id == GreetingDelivery.id)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        lease_until = now + timedelta(seconds=lease_seconds)
        for delivery in deliveries:
            delivery.status = "processing"
            delivery.lease_owner = worker_id
            delivery.lease_expires_at = lease_until
            delivery.attempts += 1
    return deliveries
