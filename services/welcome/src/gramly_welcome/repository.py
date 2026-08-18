from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Select, and_, func, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Channel,
    Contact,
    GreetingDelivery,
    InboxEvent,
    JoinRequest,
    ManagedBot,
    QueueStatus,
    WelcomeMedia,
    WelcomeMessageVersion,
)


@dataclass(frozen=True)
class BotWebhookIdentity:
    id: int
    public_id: uuid.UUID
    path_secret: str
    webhook_secret: str


@dataclass(frozen=True)
class DeliveryContext:
    delivery: GreetingDelivery
    bot: ManagedBot
    channel: Channel
    contact: Contact
    version: WelcomeMessageVersion
    media: list[WelcomeMedia]


@dataclass(frozen=True)
class JoinRequestContext:
    request: JoinRequest
    bot: ManagedBot
    channel: Channel
    contact: Contact


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


def _inbox_claim_query(now: datetime, per_source_limit: int) -> Select[tuple[InboxEvent]]:
    sources = (
        select(InboxEvent.source_key)
        .where(
            InboxEvent.status.in_((QueueStatus.PENDING.value, QueueStatus.RETRY.value)),
            InboxEvent.available_at <= now,
        )
        .distinct()
        .cte("eligible_sources")
    )
    candidate = (
        select(InboxEvent.id, InboxEvent.available_at)
        .where(
            InboxEvent.source_key == sources.c.source_key,
            InboxEvent.status.in_((QueueStatus.PENDING.value, QueueStatus.RETRY.value)),
            InboxEvent.available_at <= now,
        )
        .order_by(InboxEvent.available_at, InboxEvent.id)
        .limit(per_source_limit)
        .lateral("source_candidates")
    )
    candidates = (
        select(candidate.c.id, candidate.c.available_at)
        .select_from(sources.join(candidate, true()))
        .cte("eligible_candidates")
    )
    return (
        select(InboxEvent)
        .join(candidates, candidates.c.id == InboxEvent.id)
        .order_by(candidates.c.available_at, candidates.c.id)
        .with_for_update(of=InboxEvent, skip_locked=True)
    )


def _expired_inbox_claim_query(now: datetime) -> Select[tuple[InboxEvent]]:
    return (
        select(InboxEvent)
        .where(
            InboxEvent.status == QueueStatus.PROCESSING.value,
            InboxEvent.lease_expires_at < now,
        )
        .order_by(InboxEvent.lease_expires_at, InboxEvent.id)
        .with_for_update(of=InboxEvent, skip_locked=True)
    )


async def claim_inbox_batch(
    session: AsyncSession, *, worker_id: str, limit: int, lease_seconds: int
) -> list[InboxEvent]:
    now = datetime.now(UTC)
    async with session.begin():
        # Recover expired leases separately so the hot pending path can use a
        # narrow partial index without an expensive OR across queue states.
        events = list(
            (await session.scalars(_expired_inbox_claim_query(now).limit(limit))).all()
        )
        remaining = limit - len(events)
        if remaining:
            # LATERAL takes a bounded slice from every active source. This
            # preserves noisy-neighbour fairness without ranking and sorting
            # every pending event on each poll.
            events.extend(
                (
                    await session.scalars(
                        _inbox_claim_query(now, per_source_limit=limit * 2).limit(remaining)
                    )
                ).all()
            )
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
        ranked = (
            select(
                GreetingDelivery.id,
                func.row_number()
                .over(
                    partition_by=GreetingDelivery.bot_id,
                    order_by=(GreetingDelivery.due_at, GreetingDelivery.id),
                )
                .label("bot_rank"),
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
            .subquery()
        )
        deliveries = list(
            (
                await session.scalars(
                    select(GreetingDelivery)
                    .join(ranked, ranked.c.id == GreetingDelivery.id)
                    .where(ranked.c.bot_rank <= 3)
                    .order_by(GreetingDelivery.due_at, GreetingDelivery.id)
                    .limit(limit)
                    .with_for_update(of=GreetingDelivery, skip_locked=True)
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


async def load_delivery_context(session: AsyncSession, delivery_id: int) -> DeliveryContext | None:
    delivery = await session.get(GreetingDelivery, delivery_id)
    if delivery is None or delivery.version_id is None:
        return None
    bot = await session.get(ManagedBot, delivery.bot_id)
    channel = await session.get(Channel, delivery.channel_id)
    contact = await session.get(Contact, delivery.contact_id)
    version = await session.get(WelcomeMessageVersion, delivery.version_id)
    if bot is None or channel is None or contact is None or version is None:
        return None
    media = list(
        (
            await session.scalars(
                select(WelcomeMedia)
                .where(WelcomeMedia.version_id == version.id)
                .order_by(WelcomeMedia.position, WelcomeMedia.id)
            )
        ).all()
    )
    return DeliveryContext(delivery, bot, channel, contact, version, media)


async def defer_delivery(
    session: AsyncSession, delivery_id: int, worker_id: str, *, delay_seconds: int, error: str
) -> None:
    await session.execute(
        update(GreetingDelivery)
        .where(GreetingDelivery.id == delivery_id, GreetingDelivery.lease_owner == worker_id)
        .values(
            status="retry",
            due_at=datetime.now(UTC) + timedelta(seconds=max(1, delay_seconds)),
            lease_owner=None,
            lease_expires_at=None,
            error=error[:500],
        )
    )
    await session.commit()


async def finish_delivery(
    session: AsyncSession,
    delivery_id: int,
    worker_id: str,
    *,
    success: bool,
    error: str = "",
) -> bool:
    now = datetime.now(UTC)
    contact_id = await session.scalar(
        update(GreetingDelivery)
        .where(
            GreetingDelivery.id == delivery_id,
            GreetingDelivery.status == "processing",
            GreetingDelivery.lease_owner == worker_id,
        )
        .values(
            status="sent" if success else "failed",
            sent_at=now if success else None,
            error=error[:500],
            lease_owner=None,
            lease_expires_at=None,
        )
        .returning(GreetingDelivery.contact_id)
    )
    if contact_id is None:
        await session.commit()
        return False
    await session.execute(
        update(Contact)
        .where(Contact.id == contact_id)
        .values(
            delivery_status="live" if success else "dead",
            last_delivery_at=now,
            last_error="" if success else error[:500],
        )
    )
    await session.commit()
    return True


async def claim_join_request_batch(
    session: AsyncSession, *, worker_id: str, limit: int, lease_seconds: int
) -> list[JoinRequest]:
    now = datetime.now(UTC)
    async with session.begin():
        ranked = (
            select(
                JoinRequest.id,
                func.row_number()
                .over(
                    partition_by=JoinRequest.bot_id,
                    order_by=(JoinRequest.due_at, JoinRequest.id),
                )
                .label("bot_rank"),
            )
            .where(
                or_(
                    and_(JoinRequest.status == "scheduled", JoinRequest.due_at <= now),
                    and_(JoinRequest.status == "processing", JoinRequest.lease_expires_at < now),
                )
            )
            .subquery()
        )
        requests = list(
            (
                await session.scalars(
                    select(JoinRequest)
                    .join(ranked, ranked.c.id == JoinRequest.id)
                    .where(ranked.c.bot_rank <= 3)
                    .order_by(JoinRequest.due_at, JoinRequest.id)
                    .limit(limit)
                    .with_for_update(of=JoinRequest, skip_locked=True)
                )
            ).all()
        )
        lease_until = now + timedelta(seconds=lease_seconds)
        for request in requests:
            request.status = "processing"
            request.lease_owner = worker_id
            request.lease_expires_at = lease_until
            request.attempts += 1
    return requests


async def load_join_request_context(
    session: AsyncSession, request_id: int
) -> JoinRequestContext | None:
    request = await session.get(JoinRequest, request_id)
    if request is None:
        return None
    bot = await session.get(ManagedBot, request.bot_id)
    channel = await session.get(Channel, request.channel_id)
    contact = await session.get(Contact, request.contact_id)
    if bot is None or channel is None or contact is None:
        return None
    return JoinRequestContext(request, bot, channel, contact)


async def defer_join_request(
    session: AsyncSession, request_id: int, worker_id: str, *, delay_seconds: int, error: str
) -> None:
    await session.execute(
        update(JoinRequest)
        .where(JoinRequest.id == request_id, JoinRequest.lease_owner == worker_id)
        .values(
            status="scheduled",
            due_at=datetime.now(UTC) + timedelta(seconds=max(1, delay_seconds)),
            lease_owner=None,
            lease_expires_at=None,
            error=error[:500],
        )
    )
    await session.commit()


async def finish_join_request(
    session: AsyncSession,
    request_id: int,
    worker_id: str,
    *,
    success: bool,
    error: str = "",
) -> bool:
    result = await session.execute(
        update(JoinRequest)
        .where(
            JoinRequest.id == request_id,
            JoinRequest.status == "processing",
            JoinRequest.lease_owner == worker_id,
        )
        .values(
            status="approved" if success else "failed",
            processed_at=datetime.now(UTC),
            lease_owner=None,
            lease_expires_at=None,
            error=error[:500],
        )
    )
    await session.commit()
    return bool(cast(CursorResult[tuple[int]], result).rowcount)


async def queue_snapshot(session: AsyncSession) -> dict[str, tuple[int, int, float]]:
    now = datetime.now(UTC)
    queries: dict[str, Select[Any]] = {
        "events": select(func.count(InboxEvent.id), func.min(InboxEvent.available_at)).where(
            InboxEvent.status.in_(("pending", "retry", "processing"))
        ),
        "deliveries": select(
            func.count(GreetingDelivery.id), func.min(GreetingDelivery.due_at)
        ).where(GreetingDelivery.status.in_(("scheduled", "retry", "processing"))),
        "approvals": select(func.count(JoinRequest.id), func.min(JoinRequest.due_at)).where(
            JoinRequest.status.in_(("scheduled", "processing"))
        ),
    }
    result: dict[str, tuple[int, int, float]] = {}
    for queue, query in queries.items():
        count, oldest = (await session.execute(query)).one()
        age = max(0.0, (now - oldest).total_seconds()) if oldest is not None else 0.0
        dead = 0
        if queue == "events":
            dead = int(
                await session.scalar(
                    select(func.count(InboxEvent.id)).where(InboxEvent.status == "dead")
                )
                or 0
            )
        result[queue] = (int(count), dead, age)
    return result
