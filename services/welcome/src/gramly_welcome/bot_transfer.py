from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from .commercial import access_for_owner
from .models import (
    AdImpression,
    Broadcast,
    DepartureEvent,
    EventLog,
    ManagedBot,
    Owner,
    OwnerNotification,
    RotationChannel,
    WelcomeDraft,
)


class BotTransferError(RuntimeError):
    pass


@dataclass(frozen=True)
class BotTransferResult:
    bot_id: int
    source_owner_id: int
    target_owner_id: int
    moved: dict[str, int]


TRANSFERRED_OWNER_MODELS = (
    WelcomeDraft,
    Broadcast,
    DepartureEvent,
    RotationChannel,
    AdImpression,
    EventLog,
)


async def transfer_bot(
    session: AsyncSession,
    *,
    bot_id: int,
    target_owner_id: int,
    admin_username: str,
    reason: str,
    now: datetime | None = None,
) -> BotTransferResult:
    """Move one bot and its bot-scoped product data in the caller's transaction."""
    current = now or datetime.now(UTC)
    bot = await session.scalar(
        select(ManagedBot).where(ManagedBot.id == bot_id).with_for_update()
    )
    if bot is None:
        raise BotTransferError("Bot not found")
    if bot.owner_id == target_owner_id:
        raise BotTransferError("Bot already belongs to this owner")

    owners = list(
        (
            await session.scalars(
                select(Owner)
                .where(Owner.id.in_((bot.owner_id, target_owner_id)))
                .order_by(Owner.id)
                .with_for_update()
            )
        ).all()
    )
    owner_by_id = {owner.id: owner for owner in owners}
    source_owner = owner_by_id.get(bot.owner_id)
    target_owner = owner_by_id.get(target_owner_id)
    if source_owner is None or target_owner is None:
        raise BotTransferError("Source or target owner not found")

    access = await access_for_owner(session, target_owner_id, now=current)
    target_bot_count = int(
        await session.scalar(
            select(func.count(ManagedBot.id)).where(ManagedBot.owner_id == target_owner_id)
        )
        or 0
    )
    if not access.entitled or target_bot_count >= access.max_bots:
        raise BotTransferError("Target owner has reached the bot limit")

    source_owner_id = bot.owner_id
    moved: dict[str, int] = {}
    for model in TRANSFERRED_OWNER_MODELS:
        result = await session.execute(
            update(model)
            .where(model.bot_id == bot.id, model.owner_id == source_owner_id)
            .values(owner_id=target_owner_id)
        )
        moved[model.__tablename__] = int(cast(CursorResult[Any], result).rowcount or 0)

    bot.owner_id = target_owner_id
    bot.updated_at = current
    bot_label = f"@{bot.username}" if bot.username else bot.display_name
    transfer_id = uuid.uuid4().hex
    session.add_all(
        [
            OwnerNotification(
                owner_id=source_owner_id,
                kind="bot_transfer",
                dedupe_key=f"bot-transfer:{transfer_id}:source",
                sequence=0,
                payload={
                    "title": "Бот передан",
                    "body": f"{bot_label} передан другому владельцу. Причина: {reason}",
                },
                status="pending",
                due_at=current,
            ),
            OwnerNotification(
                owner_id=target_owner_id,
                kind="bot_transfer",
                dedupe_key=f"bot-transfer:{transfer_id}:target",
                sequence=0,
                payload={
                    "title": "Бот добавлен",
                    "body": f"{bot_label} передан вам. Каналы, приветствия и база уже доступны.",
                },
                status="pending",
                due_at=current,
            ),
            EventLog(
                bot_id=bot.id,
                owner_id=target_owner_id,
                event_type="welcome_admin_mutation",
                level="security",
                message="bot_transferred",
                context={
                    "admin": admin_username,
                    "bot_id": bot.id,
                    "source_owner_id": source_owner_id,
                    "target_owner_id": target_owner_id,
                    "reason": reason,
                    "moved": moved,
                },
            ),
        ]
    )
    await session.flush()
    return BotTransferResult(bot.id, source_owner_id, target_owner_id, moved)
