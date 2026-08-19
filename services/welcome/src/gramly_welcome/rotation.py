from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .commercial import access_for_owner
from .metrics import ROTATION_CONVERSIONS
from .models import (
    Channel,
    Contact,
    DepartureEvent,
    FlowDelivery,
    ManagedBot,
    Owner,
    Plan,
    RotationChannel,
    RotationConversion,
    RotationImpression,
    RotationRecommendation,
    Subscription,
)

MAX_ROTATION_DESTINATIONS = 7
MAX_PRIORITY_CHANNELS = 7
TERMINAL_FLOW_STATUSES = ("completed", "partial", "failed", "cancelled", "unreachable")


@dataclass(frozen=True)
class RotationContext:
    recommendation: RotationRecommendation
    departure: DepartureEvent
    source_bot: ManagedBot
    source_channel: Channel
    contact: Contact


@dataclass(frozen=True)
class RotationDestination:
    rotation: RotationChannel
    channel: Channel
    bot: ManagedBot


def merge_rotation_destinations(
    priority: list[RotationDestination], pool: list[RotationDestination]
) -> list[RotationDestination]:
    result: list[RotationDestination] = []
    seen: set[int] = set()
    for destination in [*priority, *pool]:
        if destination.channel.id in seen:
            continue
        seen.add(destination.channel.id)
        result.append(destination)
        if len(result) == MAX_ROTATION_DESTINATIONS:
            break
    return result


async def sync_rotation_channel(
    session: AsyncSession, *, bot: ManagedBot, channel_id: int, active: bool
) -> None:
    await session.execute(
        insert(RotationChannel)
        .values(
            owner_id=bot.owner_id,
            bot_id=bot.id,
            channel_id=channel_id,
            is_enabled=active,
        )
        .on_conflict_do_update(
            constraint="rotation_channel_channel_id_key",
            set_={"is_enabled": active, "updated_at": datetime.now(UTC)},
        )
    )


async def schedule_rotation_recommendation(
    session: AsyncSession,
    *,
    departure_id: int,
    due_at: datetime | None = None,
) -> None:
    await session.execute(
        insert(RotationRecommendation)
        .values(departure_id=departure_id, status="scheduled", due_at=due_at or datetime.now(UTC))
        .on_conflict_do_nothing(constraint="rotation_recommendation_departure_id_key")
    )


async def set_priority_channel(
    session: AsyncSession, *, owner_id: int, channel_id: int, priority: bool
) -> None:
    access = await access_for_owner(session, owner_id)
    if not access.entitlements.get("rotation", False):
        raise ValueError("Rotation is available on Business")
    target = await session.scalar(
        select(RotationChannel)
        .where(
            RotationChannel.owner_id == owner_id,
            RotationChannel.channel_id == channel_id,
            RotationChannel.is_enabled.is_(True),
        )
        .with_for_update()
    )
    if target is None:
        raise ValueError("Rotation channel was not found")
    if priority and not target.is_priority:
        count = int(
            await session.scalar(
                select(func.count(RotationChannel.id)).where(
                    RotationChannel.owner_id == owner_id,
                    RotationChannel.is_enabled.is_(True),
                    RotationChannel.is_priority.is_(True),
                )
            )
            or 0
        )
        if count >= MAX_PRIORITY_CHANNELS:
            raise ValueError("Up to 7 priority channels are allowed")
    target.is_priority = priority
    await session.commit()


async def owner_rotation_channels(
    session: AsyncSession, owner_id: int
) -> list[tuple[RotationChannel, Channel]]:
    rows = (
        await session.execute(
            select(RotationChannel, Channel)
            .join(Channel, Channel.id == RotationChannel.channel_id)
            .where(RotationChannel.owner_id == owner_id, RotationChannel.is_enabled.is_(True))
            .order_by(RotationChannel.is_priority.desc(), Channel.title, Channel.id)
        )
    ).all()
    return [(rotation, channel) for rotation, channel in rows]


async def claim_rotation_batch(
    session: AsyncSession, *, worker_id: str, limit: int, lease_seconds: int
) -> list[RotationRecommendation]:
    now = datetime.now(UTC)
    async with session.begin():
        rows = list(
            (
                await session.scalars(
                    select(RotationRecommendation)
                    .join(DepartureEvent, DepartureEvent.id == RotationRecommendation.departure_id)
                    .outerjoin(FlowDelivery, FlowDelivery.id == DepartureEvent.farewell_delivery_id)
                    .where(
                        or_(
                            and_(
                                RotationRecommendation.status.in_(("scheduled", "retry")),
                                RotationRecommendation.due_at <= now,
                            ),
                            and_(
                                RotationRecommendation.status == "processing",
                                RotationRecommendation.lease_expires_at < now,
                            ),
                        ),
                        or_(
                            DepartureEvent.farewell_delivery_id.is_(None),
                            FlowDelivery.status.in_(TERMINAL_FLOW_STATUSES),
                        ),
                    )
                    .order_by(RotationRecommendation.due_at, RotationRecommendation.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True, of=RotationRecommendation)
                )
            ).all()
        )
        for row in rows:
            row.status = "processing"
            row.attempts += 1
            row.lease_owner = worker_id
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
    return rows


async def load_rotation_context(
    session: AsyncSession, recommendation_id: int
) -> RotationContext | None:
    row = (
        await session.execute(
            select(RotationRecommendation, DepartureEvent, ManagedBot, Channel, Contact)
            .join(DepartureEvent, DepartureEvent.id == RotationRecommendation.departure_id)
            .join(ManagedBot, ManagedBot.id == DepartureEvent.bot_id)
            .join(Channel, Channel.id == DepartureEvent.channel_id)
            .join(Contact, Contact.id == DepartureEvent.contact_id)
            .where(RotationRecommendation.id == recommendation_id)
        )
    ).one_or_none()
    return RotationContext(*row) if row is not None else None


async def eligible_rotation_destinations(
    session: AsyncSession, context: RotationContext
) -> list[RotationDestination]:
    now = datetime.now(UTC)
    common = (
        select(RotationChannel, Channel, ManagedBot)
        .join(Channel, Channel.id == RotationChannel.channel_id)
        .join(ManagedBot, ManagedBot.id == RotationChannel.bot_id)
        .join(Subscription, Subscription.owner_id == RotationChannel.owner_id)
        .join(Plan, Plan.id == Subscription.plan_id)
        .where(
            RotationChannel.is_enabled.is_(True),
            Channel.is_active.is_(True),
            Channel.can_invite_users.is_(True),
            ManagedBot.is_active.is_(True),
            RotationChannel.channel_id != context.source_channel.id,
            Subscription.status == "active",
            Subscription.starts_at <= now,
            Subscription.ends_at.is_not(None),
            Subscription.ends_at > now,
            Plan.slug == "business",
            Plan.is_active.is_(True),
        )
    )
    priority_rows = (
        await session.execute(
            common.where(
                RotationChannel.owner_id == context.departure.owner_id,
                RotationChannel.is_priority.is_(True),
            )
            .order_by(Channel.id)
            .limit(MAX_ROTATION_DESTINATIONS)
        )
    ).all()
    priority = [(rotation, channel, bot) for rotation, channel, bot in priority_rows]
    selected_ids = [row[0].channel_id for row in priority]
    remaining = MAX_ROTATION_DESTINATIONS - len(priority)
    random_rows: list[tuple[RotationChannel, Channel, ManagedBot]] = []
    if remaining:
        statement = common
        if selected_ids:
            statement = statement.where(RotationChannel.channel_id.not_in(selected_ids))
        rows = (await session.execute(statement.order_by(func.random()).limit(remaining))).all()
        random_rows = [(rotation, channel, bot) for rotation, channel, bot in rows]
    return merge_rotation_destinations(
        [RotationDestination(*row) for row in priority],
        [RotationDestination(*row) for row in random_rows],
    )


async def store_rotation_invite_link(
    session: AsyncSession, rotation_id: int, invite_link: str
) -> None:
    await session.execute(
        update(RotationChannel)
        .where(RotationChannel.id == rotation_id)
        .values(
            invite_link=invite_link,
            last_verified_at=datetime.now(UTC),
            last_error="",
            updated_at=datetime.now(UTC),
        )
    )
    await session.commit()


async def mark_rotation_channel_error(session: AsyncSession, rotation_id: int, error: str) -> None:
    await session.execute(
        update(RotationChannel)
        .where(RotationChannel.id == rotation_id)
        .values(last_error=error[:500], updated_at=datetime.now(UTC))
    )
    await session.commit()


async def defer_rotation(
    session: AsyncSession,
    recommendation_id: int,
    worker_id: str,
    *,
    delay_seconds: int,
    error: str,
) -> None:
    await session.execute(
        update(RotationRecommendation)
        .where(
            RotationRecommendation.id == recommendation_id,
            RotationRecommendation.status == "processing",
            RotationRecommendation.lease_owner == worker_id,
        )
        .values(
            status="retry",
            due_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
            lease_owner=None,
            lease_expires_at=None,
            error=error[:500],
        )
    )
    await session.commit()


async def finish_rotation(
    session: AsyncSession,
    context: RotationContext,
    worker_id: str,
    *,
    status: str,
    destinations: list[RotationDestination] | None = None,
    error: str = "",
) -> None:
    target = await session.scalar(
        select(RotationRecommendation)
        .where(
            RotationRecommendation.id == context.recommendation.id,
            RotationRecommendation.status == "processing",
            RotationRecommendation.lease_owner == worker_id,
        )
        .with_for_update()
    )
    if target is None:
        return
    target.status = status
    target.sent_at = datetime.now(UTC) if status == "sent" else None
    target.lease_owner = None
    target.lease_expires_at = None
    target.error = error[:500]
    if status == "sent" and destinations:
        for destination in destinations:
            session.add(
                RotationImpression(
                    recommendation_id=target.id,
                    source_owner_id=context.departure.owner_id,
                    destination_owner_id=destination.rotation.owner_id,
                    destination_channel_id=destination.channel.id,
                    telegram_user_id=context.contact.telegram_id,
                    invite_link_snapshot=destination.rotation.invite_link,
                )
            )
    await session.commit()


async def record_rotation_conversion(
    session: AsyncSession,
    *,
    destination_channel_id: int,
    telegram_user_id: int,
    telegram_update_id: int,
    invite_link: str,
) -> bool:
    destination_owner_id = await session.scalar(
        select(RotationChannel.owner_id).where(RotationChannel.channel_id == destination_channel_id)
    )
    owner_telegram_id = (
        await session.scalar(select(Owner.telegram_id).where(Owner.id == destination_owner_id))
        if destination_owner_id is not None
        else None
    )
    if owner_telegram_id == telegram_user_id:
        return False
    impression = await session.scalar(
        select(RotationImpression)
        .where(
            RotationImpression.destination_channel_id == destination_channel_id,
            RotationImpression.telegram_user_id == telegram_user_id,
            RotationImpression.invite_link_snapshot == invite_link,
        )
        .order_by(RotationImpression.shown_at.desc(), RotationImpression.id.desc())
        .limit(1)
    )
    if impression is None:
        return False
    inserted = await session.scalar(
        insert(RotationConversion)
        .values(
            impression_id=impression.id,
            destination_channel_id=destination_channel_id,
            telegram_user_id=telegram_user_id,
            telegram_update_id=telegram_update_id,
        )
        .on_conflict_do_nothing(constraint="uq_rotation_conversion_user_channel")
        .returning(RotationConversion.id)
    )
    if inserted is not None:
        ROTATION_CONVERSIONS.inc()
        return True
    return False


async def rotation_statistics(session: AsyncSession, owner_id: int) -> dict[str, int]:
    impressions = int(
        await session.scalar(
            select(func.count(RotationImpression.id)).where(
                RotationImpression.destination_owner_id == owner_id
            )
        )
        or 0
    )
    conversions = int(
        await session.scalar(
            select(func.count(RotationConversion.id))
            .join(RotationImpression, RotationImpression.id == RotationConversion.impression_id)
            .where(RotationImpression.destination_owner_id == owner_id)
        )
        or 0
    )
    return {"impressions": impressions, "conversions": conversions}
