from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Channel,
    Contact,
    EventLog,
    GreetingDelivery,
    InboxEvent,
    JoinRequest,
    ManagedBot,
    WelcomeMessageVersion,
)


def _telegram_user(payload: dict[str, Any]) -> dict[str, Any] | None:
    for event_name in ("chat_join_request", "chat_member"):
        event = payload.get(event_name)
        if not isinstance(event, dict):
            continue
        if event_name == "chat_join_request":
            user = event.get("from")
        else:
            member = event.get("new_chat_member")
            user = member.get("user") if isinstance(member, dict) else None
        if isinstance(user, dict):
            return user
    message = payload.get("message")
    user = message.get("from") if isinstance(message, dict) else None
    return user if isinstance(user, dict) else None


async def _upsert_contact(session: AsyncSession, bot_id: int, user: dict[str, Any], bot_started: bool = False) -> int:
    statement = (
        insert(Contact)
        .values(
            bot_id=bot_id,
            telegram_id=int(user["id"]),
            username=str(user.get("username") or ""),
            first_name=str(user.get("first_name") or ""),
            last_name=str(user.get("last_name") or ""),
            language_code=str(user.get("language_code") or "unknown"),
            bot_started=bot_started,
        )
        .on_conflict_do_update(
            constraint="uq_contact_bot_telegram",
            set_={
                "username": str(user.get("username") or ""),
                "first_name": str(user.get("first_name") or ""),
                "last_name": str(user.get("last_name") or ""),
                "language_code": str(user.get("language_code") or "unknown"),
                "bot_started": True if bot_started else Contact.bot_started,
                "last_seen_at": datetime.now(UTC),
            },
        )
        .returning(Contact.id)
    )
    return int(await session.scalar(statement))


async def _upsert_channel(session: AsyncSession, bot_id: int, chat: dict[str, Any]) -> int:
    statement = (
        insert(Channel)
        .values(
            bot_id=bot_id,
            telegram_id=int(chat["id"]),
            title=str(chat.get("title") or chat["id"]),
            username=str(chat.get("username") or ""),
            is_active=True,
        )
        .on_conflict_do_update(
            constraint="uq_channel_bot_telegram",
            set_={
                "title": str(chat.get("title") or chat["id"]),
                "username": str(chat.get("username") or ""),
                "is_active": True,
            },
        )
        .returning(Channel.id)
    )
    return int(await session.scalar(statement))


async def _schedule_greeting(
    session: AsyncSession, bot: ManagedBot, channel_id: int, contact_id: int, event_key: str
) -> None:
    # Serialise the short coalescing check for this contact. A join approval and
    # Telegram's following chat_member update may arrive on different API pods.
    await session.execute(select(Contact.id).where(Contact.id == contact_id).with_for_update())
    version_id = await session.scalar(
        select(WelcomeMessageVersion.id).where(
            WelcomeMessageVersion.bot_id == bot.id,
            WelcomeMessageVersion.is_active.is_(True),
        )
    )
    if version_id is None:
        return
    recent = await session.scalar(
        select(GreetingDelivery.id)
        .where(
            GreetingDelivery.bot_id == bot.id,
            GreetingDelivery.channel_id == channel_id,
            GreetingDelivery.contact_id == contact_id,
            GreetingDelivery.created_at >= datetime.now(UTC) - timedelta(minutes=5),
            GreetingDelivery.status != "cancelled",
        )
        .limit(1)
    )
    if recent is not None:
        return
    await session.execute(
        insert(GreetingDelivery)
        .values(
            bot_id=bot.id,
            channel_id=channel_id,
            contact_id=contact_id,
            version_id=version_id,
            event_key=event_key,
            status="scheduled",
            due_at=datetime.now(UTC) + timedelta(seconds=bot.welcome_delay_seconds),
        )
        .on_conflict_do_nothing(constraint="uq_delivery_bot_event")
    )


async def _process_bot_membership(
    session: AsyncSession, bot: ManagedBot, event: dict[str, Any]
) -> bool:
    chat = event.get("chat")
    member = event.get("new_chat_member")
    if not isinstance(chat, dict) or not isinstance(member, dict):
        return False
    status = str(member.get("status") or "")
    chat_type = str(chat.get("type") or "")
    if chat_type == "private" and "id" in chat:
        active = status in {"member", "administrator"}
        await session.execute(
            update(Contact)
            .where(Contact.bot_id == bot.id, Contact.telegram_id == int(chat["id"]))
            .values(
                delivery_status="live" if active else "dead",
                bot_started=active,
                last_delivery_at=datetime.now(UTC),
                last_error="" if active else "user_blocked_bot",
            )
        )
        return True
    if chat_type not in {"channel", "supergroup"} or "id" not in chat:
        return False
    channel_id = await _upsert_channel(session, bot.id, chat)
    active = status in {"administrator", "creator"}
    await session.execute(
        update(Channel)
        .where(Channel.id == channel_id)
        .values(
            is_active=active,
            can_invite_users=bool(member.get("can_invite_users", False)),
        )
    )
    await session.execute(
        insert(EventLog).values(
            bot_id=bot.id,
            owner_id=bot.owner_id,
            event_type="channel_connected" if active else "channel_disconnected",
            level="info",
            message="Telegram channel membership changed",
            context={"chat_id": int(chat["id"]), "active": active},
        )
    )
    return True


async def process_event(session: AsyncSession, event: InboxEvent) -> None:
    # The owner-facing setup bot remains on the Django control plane during the
    # parallel migration. Its durable events are retained and the cutover MR
    # will attach the control-plane consumer before routing that webhook here.
    if event.bot_id is None:
        raise RuntimeError("interface consumer is not enabled before cutover")

    bot = await session.get(ManagedBot, event.bot_id)
    if bot is None or not bot.is_active:
        return
    payload = event.payload
    bot_membership = payload.get("my_chat_member")
    if isinstance(bot_membership, dict) and await _process_bot_membership(session, bot, bot_membership):
        return
    user = _telegram_user(payload)
    if user is None or "id" not in user:
        return

    message = payload.get("message")
    if isinstance(message, dict) and isinstance(message.get("chat"), dict):
        chat = message["chat"]
        if chat.get("type") == "private":
            await _upsert_contact(session, bot.id, user, bot_started=True)
            return

    join = payload.get("chat_join_request")
    if isinstance(join, dict) and isinstance(join.get("chat"), dict):
        contact_id = await _upsert_contact(session, bot.id, user)
        channel_id = await _upsert_channel(session, bot.id, join["chat"])
        due_at = datetime.now(UTC) + timedelta(seconds=bot.approval_delay_seconds) if bot.auto_approve else None
        await session.execute(
            insert(JoinRequest)
            .values(
                bot_id=bot.id,
                channel_id=channel_id,
                contact_id=contact_id,
                telegram_update_id=event.update_id,
                status="scheduled" if bot.auto_approve else "pending",
                due_at=due_at,
            )
            .on_conflict_do_nothing(constraint="uq_join_bot_update")
        )
        return

    member = payload.get("chat_member")
    if isinstance(member, dict) and isinstance(member.get("chat"), dict):
        old = member.get("old_chat_member") or {}
        new = member.get("new_chat_member") or {}
        old_status = str(old.get("status") or "")
        new_status = str(new.get("status") or "")
        active = {"member", "administrator", "creator", "restricted"}
        contact_id = await _upsert_contact(session, bot.id, user)
        channel_id = await _upsert_channel(session, bot.id, member["chat"])
        if old_status not in active and new_status in active:
            await _schedule_greeting(session, bot, channel_id, contact_id, f"chat-member:{event.update_id}")
        return

    await session.execute(
        insert(EventLog).values(
            bot_id=bot.id,
            owner_id=bot.owner_id,
            event_type="ignored_update",
            level="info",
            message="Telegram update type is not actionable",
            context={"update_id": event.update_id},
        )
    )
