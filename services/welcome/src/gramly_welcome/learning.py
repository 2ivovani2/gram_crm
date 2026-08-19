from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .models import (
    Announcement,
    ContextualHelp,
    Manual,
    ManualSection,
    Owner,
    OwnerNotification,
    Plan,
    Subscription,
    Tip,
    TipSession,
    TipViewState,
)

OPEN_NOTIFICATION_STATUSES = ("pending", "processing", "retry")


@dataclass(frozen=True)
class HelpSnapshot:
    feature_key: str
    title: str
    body: str
    manual_url: str
    session_id: uuid.UUID | None
    tip: Tip | None
    tip_index: int
    tip_count: int


def order_tip_ids(tip_ids: list[int], last_tip_id: int | None) -> list[int]:
    """Randomize once while preventing the previous session's first tip from repeating."""
    result = list(tip_ids)
    secrets.SystemRandom().shuffle(result)
    if len(result) > 1 and result[0] == last_tip_id:
        result[0], result[1] = result[1], result[0]
    return result


async def _owner_plan_slug(session: AsyncSession, owner_id: int, now: datetime) -> str:
    slug = await session.scalar(
        select(Plan.slug)
        .join(Subscription, Subscription.plan_id == Plan.id)
        .where(
            Subscription.owner_id == owner_id,
            Subscription.status == "active",
            or_(Subscription.ends_at.is_(None), Subscription.ends_at > now),
        )
    )
    return str(slug or "free")


async def schedule_learning_notifications(
    session: AsyncSession, owner_id: int, *, now: datetime | None = None
) -> int:
    """Create durable post-response notifications without ever sending Telegram I/O."""
    now = now or datetime.now(UTC)
    owner = await session.get(Owner, owner_id)
    if owner is None:
        return 0
    queued = 0
    sequence = 0
    if not owner.guide_completed:
        manual = await session.scalar(
            select(Manual)
            .where(Manual.is_onboarding.is_(True), Manual.is_active.is_(True))
            .order_by(Manual.sort_order, Manual.id)
            .limit(1)
        )
        payload: dict[str, Any] = {
            "title": "Быстрый старт GramlyHello",
            "body": (
                "Подключите бота, добавьте его администратором в канал и опубликуйте "
                "первую цепочку. Настройки всегда доступны из главного меню."
            ),
        }
        if manual is not None:
            payload.update(
                title=manual.title,
                body=manual.description,
                button_text="Открыть обучение",
                button_url=manual.telegraph_url,
            )
        result = await session.execute(
            insert(OwnerNotification)
            .values(
                owner_id=owner_id,
                kind="onboarding",
                dedupe_key="onboarding:v1",
                sequence=sequence,
                payload=payload,
                status="pending",
                due_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_owner_notification_dedupe")
            .returning(OwnerNotification.id)
        )
        queued += int(result.scalar_one_or_none() is not None)
        sequence += 1

    plan_slug = await _owner_plan_slug(session, owner_id, now)
    announcements = list(
        (
            await session.scalars(
                select(Announcement)
                .where(
                    Announcement.is_active.is_(True),
                    Announcement.starts_at <= now,
                    or_(Announcement.ends_at.is_(None), Announcement.ends_at > now),
                    Announcement.audience.in_(("all", plan_slug)),
                )
                .order_by(Announcement.priority.desc(), Announcement.starts_at, Announcement.id)
            )
        ).all()
    )
    for announcement in announcements:
        result = await session.execute(
            insert(OwnerNotification)
            .values(
                owner_id=owner_id,
                announcement_id=announcement.id,
                kind="announcement",
                dedupe_key=f"announcement:{announcement.id}",
                sequence=sequence,
                payload={
                    "title": announcement.title,
                    "body": announcement.body,
                    "entities": announcement.entities,
                    "button_text": announcement.button_text,
                    "button_url": announcement.button_url,
                },
                status="pending",
                due_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_owner_notification_dedupe")
            .returning(OwnerNotification.id)
        )
        queued += int(result.scalar_one_or_none() is not None)
        sequence += 1
    await session.commit()
    return queued


async def claim_notification(
    session: AsyncSession,
    worker_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int = 60,
) -> tuple[OwnerNotification, int] | None:
    now = now or datetime.now(UTC)
    earlier = aliased(OwnerNotification)
    claimable = or_(
        and_(
            OwnerNotification.status.in_(("pending", "retry")),
            OwnerNotification.due_at <= now,
        ),
        and_(
            OwnerNotification.status == "processing",
            OwnerNotification.lease_expires_at < now,
        ),
    )
    notification = await session.scalar(
        select(OwnerNotification)
        .where(
            claimable,
            ~exists(
                select(earlier.id).where(
                    earlier.owner_id == OwnerNotification.owner_id,
                    earlier.sequence < OwnerNotification.sequence,
                    earlier.status.in_(OPEN_NOTIFICATION_STATUSES),
                )
            ),
        )
        .order_by(OwnerNotification.due_at, OwnerNotification.owner_id, OwnerNotification.sequence)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if notification is None:
        await session.rollback()
        return None
    telegram_id = await session.scalar(
        select(Owner.telegram_id).where(Owner.id == notification.owner_id)
    )
    if telegram_id is None:
        notification.status = "failed"
        notification.last_error = "owner missing"
        await session.commit()
        return None
    notification.status = "processing"
    notification.attempts += 1
    notification.lease_owner = worker_id
    notification.lease_expires_at = now + timedelta(seconds=lease_seconds)
    await session.commit()
    return notification, int(telegram_id)


async def finish_notification(
    session: AsyncSession,
    notification_id: int,
    *,
    error: str | None = None,
    now: datetime | None = None,
    max_attempts: int = 8,
) -> None:
    now = now or datetime.now(UTC)
    notification = await session.get(OwnerNotification, notification_id, with_for_update=True)
    if notification is None:
        return
    notification.lease_owner = None
    notification.lease_expires_at = None
    if error is None:
        notification.status = "sent"
        notification.sent_at = now
        notification.last_error = ""
        if notification.kind == "onboarding":
            await session.execute(
                update(Owner)
                .where(Owner.id == notification.owner_id)
                .values(guide_completed=True, guide_step=1)
            )
    elif notification.attempts >= max_attempts:
        notification.status = "failed"
        notification.last_error = error[:500]
    else:
        notification.status = "retry"
        notification.last_error = error[:500]
        delay = min(3600, 5 * (2 ** max(0, notification.attempts - 1)))
        notification.due_at = now + timedelta(seconds=delay)
    await session.commit()


async def open_help_session(
    session: AsyncSession,
    owner_id: int,
    feature_key: str,
    *,
    now: datetime | None = None,
) -> HelpSnapshot:
    now = now or datetime.now(UTC)
    help_item = await session.scalar(
        select(ContextualHelp).where(
            ContextualHelp.feature_key == feature_key,
            ContextualHelp.is_active.is_(True),
        )
    )
    manual_url = ""
    if help_item is not None and help_item.section_id is not None:
        section = await session.get(ManualSection, help_item.section_id)
        manual_url = section.section_url if section and section.is_active else ""
    if not manual_url and help_item is not None and help_item.manual_id is not None:
        manual = await session.get(Manual, help_item.manual_id)
        manual_url = manual.telegraph_url if manual and manual.is_active else ""

    tips = list(
        (
            await session.scalars(
                select(Tip)
                .where(Tip.feature_key == feature_key, Tip.is_active.is_(True))
                .order_by(Tip.sort_order, Tip.id)
            )
        ).all()
    )
    state = await session.scalar(
        select(TipViewState).where(
            TipViewState.owner_id == owner_id,
            TipViewState.feature_key == feature_key,
        )
    )
    tip_ids = order_tip_ids([tip.id for tip in tips], state.last_tip_id if state else None)
    tip_by_id = {tip.id: tip for tip in tips}
    tip = tip_by_id.get(tip_ids[0]) if tip_ids else None
    tip_session: TipSession | None = None
    if tip is not None:
        tip_session = TipSession(
            public_id=uuid.uuid4(),
            owner_id=owner_id,
            feature_key=feature_key,
            tip_ids=tip_ids,
            current_index=0,
            expires_at=now + timedelta(hours=6),
        )
        session.add(tip_session)
        if state is None:
            session.add(
                TipViewState(owner_id=owner_id, feature_key=feature_key, last_tip_id=tip.id)
            )
        else:
            state.last_tip_id = tip.id
    await session.commit()
    return HelpSnapshot(
        feature_key=feature_key,
        title=help_item.title if help_item else "Подсказка по настройке",
        body=help_item.body if help_item else "Здесь появится инструкция от команды GramlyHello.",
        manual_url=manual_url,
        session_id=tip_session.public_id if tip_session else None,
        tip=tip,
        tip_index=0,
        tip_count=len(tips),
    )


async def navigate_tip_session(
    session: AsyncSession,
    owner_id: int,
    public_id: uuid.UUID,
    delta: int,
    *,
    now: datetime | None = None,
) -> HelpSnapshot | None:
    now = now or datetime.now(UTC)
    tip_session = await session.scalar(
        select(TipSession).where(
            TipSession.public_id == public_id,
            TipSession.owner_id == owner_id,
            TipSession.expires_at > now,
        )
    )
    if tip_session is None or not tip_session.tip_ids:
        return None
    new_index = (tip_session.current_index + delta) % len(tip_session.tip_ids)
    tip_session.current_index = new_index
    tip = await session.get(Tip, int(tip_session.tip_ids[new_index]))
    state = await session.scalar(
        select(TipViewState).where(
            TipViewState.owner_id == owner_id,
            TipViewState.feature_key == tip_session.feature_key,
        )
    )
    if state is not None and tip is not None:
        state.last_tip_id = tip.id
    await session.commit()
    return HelpSnapshot(
        feature_key=tip_session.feature_key,
        title="Совет по настройке",
        body="",
        manual_url="",
        session_id=tip_session.public_id,
        tip=tip,
        tip_index=new_index,
        tip_count=len(tip_session.tip_ids),
    )


async def purge_expired_tip_sessions(session: AsyncSession, *, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    ids = list(await session.scalars(select(TipSession.id).where(TipSession.expires_at < now)))
    if ids:
        await session.execute(delete(TipSession).where(TipSession.id.in_(ids)))
    await session.commit()
    return len(ids)
