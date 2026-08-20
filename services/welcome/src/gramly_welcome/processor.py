from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .flow_delivery import schedule_content_flow
from .join_request_policy import JOIN_REQUEST_MAX_TIMELINE_SECONDS, message_window
from .models import (
    Channel,
    ChannelMembership,
    Contact,
    DepartureEvent,
    EventLog,
    FeatureFlag,
    GreetingDelivery,
    InboxEvent,
    JoinRequest,
    ManagedBot,
    WelcomeMessageVersion,
)
from .repository import close_open_join_request
from .rotation import (
    record_rotation_conversion,
    schedule_rotation_recommendation,
    sync_rotation_channel,
)

ACTIONABLE_UPDATE_KEYS = (
    "my_chat_member",
    "message",
    "chat_join_request",
    "chat_member",
    "pre_checkout_query",
)

async def _join_request_greetings_enabled(session: AsyncSession, bot_id: int) -> bool:
    flag = await session.get(FeatureFlag, "join_request_greetings")
    if flag is None or not flag.enabled:
        return False
    configured = flag.config.get("bot_ids", []) if isinstance(flag.config, dict) else []
    if not isinstance(configured, list) or not configured:
        return True
    return bot_id in {int(value) for value in configured if str(value).isdigit()}


def membership_transition_flags(
    stored_status: str | None, *, old_active: bool, new_active: bool
) -> tuple[bool, bool]:
    was_active = stored_status == "active" if stored_status is not None else old_active
    return not was_active and new_active, was_active and not new_active


def _join_request_upsert(
    *,
    bot: ManagedBot,
    channel_id: int,
    contact_id: int,
    update_id: int,
    user_chat_id: int | None,
    message_window_expires_at: datetime,
    due_at: datetime | None,
    now: datetime,
) -> Any:
    """Insert a request or refresh an older still-open application.

    Telegram issues a new short-lived ``user_chat_id`` when somebody applies
    again. Keeping the old open row would discard that new messaging window,
    so the partial unique conflict is deliberately treated as a refresh. Inbox
    idempotency still prevents the same Telegram update from being processed
    twice.
    """

    status = "scheduled" if bot.auto_approve else "pending"
    statement = insert(JoinRequest).values(
        bot_id=bot.id,
        channel_id=channel_id,
        contact_id=contact_id,
        telegram_update_id=update_id,
        user_chat_id=user_chat_id,
        message_window_expires_at=message_window_expires_at,
        welcome_delivery_id=None,
        status=status,
        delay_snapshot_seconds=bot.approval_delay_seconds,
        due_at=due_at,
        attempts=0,
        lease_owner=None,
        lease_expires_at=None,
        error="",
        created_at=now,
        processed_at=None,
    )
    return statement.on_conflict_do_update(
        index_elements=[JoinRequest.channel_id, JoinRequest.contact_id],
        index_where=JoinRequest.status.in_(("pending", "scheduled", "processing")),
        set_={
            "telegram_update_id": statement.excluded.telegram_update_id,
            "user_chat_id": statement.excluded.user_chat_id,
            "message_window_expires_at": statement.excluded.message_window_expires_at,
            "welcome_delivery_id": None,
            "status": statement.excluded.status,
            "delay_snapshot_seconds": statement.excluded.delay_snapshot_seconds,
            "due_at": statement.excluded.due_at,
            "attempts": 0,
            "lease_owner": None,
            "lease_expires_at": None,
            "error": "",
            "created_at": statement.excluded.created_at,
            "processed_at": None,
        },
    ).returning(JoinRequest.id)


def is_actionable_payload(payload: dict[str, Any]) -> bool:
    return any(isinstance(payload.get(key), dict) for key in ACTIONABLE_UPDATE_KEYS)


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


async def _upsert_contact(
    session: AsyncSession, bot_id: int, user: dict[str, Any], bot_started: bool = False
) -> int:
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
    if await schedule_content_flow(
        session,
        bot=bot,
        channel_id=channel_id,
        contact_id=contact_id,
        event_key=event_key,
    ):
        return
    # Compatibility fallback for a database that has not yet migrated its
    # legacy one-message greeting into the content engine.
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
            delay_snapshot_seconds=bot.welcome_delay_seconds,
            due_at=datetime.now(UTC) + timedelta(seconds=bot.welcome_delay_seconds),
        )
        .on_conflict_do_nothing(constraint="uq_delivery_bot_event")
    )


async def _process_bot_membership(session: AsyncSession, bot: ManagedBot, event: dict[str, Any]) -> bool:
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
    await sync_rotation_channel(session, bot=bot, channel_id=channel_id, active=active)
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


async def _membership_transition(
    session: AsyncSession,
    *,
    channel_id: int,
    contact_id: int,
    update_id: int,
    old_active: bool,
    new_active: bool,
) -> tuple[bool, bool]:
    membership = await session.scalar(
        select(ChannelMembership)
        .where(
            ChannelMembership.channel_id == channel_id,
            ChannelMembership.contact_id == contact_id,
        )
        .with_for_update()
    )
    if membership is not None and update_id <= membership.last_update_id:
        return False, False
    joined, left = membership_transition_flags(
        membership.status if membership is not None else None,
        old_active=old_active,
        new_active=new_active,
    )
    now = datetime.now(UTC)
    if membership is None:
        membership = ChannelMembership(
            channel_id=channel_id,
            contact_id=contact_id,
            status="active" if new_active else "left",
            last_update_id=update_id,
            joined_at=now if new_active else None,
            left_at=now if not new_active else None,
            updated_at=now,
        )
        session.add(membership)
    else:
        membership.status = "active" if new_active else "left"
        membership.last_update_id = update_id
        membership.updated_at = now
        if new_active:
            membership.joined_at = now
        else:
            membership.left_at = now
    return joined, left


async def _schedule_departure(
    session: AsyncSession,
    *,
    event: InboxEvent,
    bot: ManagedBot,
    channel_id: int,
    contact_id: int,
    reason: str,
) -> None:
    departure_id = await session.scalar(
        insert(DepartureEvent)
        .values(
            bot_id=bot.id,
            owner_id=bot.owner_id,
            channel_id=channel_id,
            contact_id=contact_id,
            telegram_update_id=event.update_id,
            reason=reason,
        )
        .on_conflict_do_nothing(constraint="uq_departure_bot_update")
        .returning(DepartureEvent.id)
    )
    if departure_id is None:
        return
    delivery_id = await schedule_content_flow(
        session,
        bot=bot,
        channel_id=channel_id,
        contact_id=contact_id,
        event_key=f"farewell:{event.update_id}",
        kind="farewell",
    )
    if delivery_id is not None:
        await session.execute(
            update(DepartureEvent)
            .where(DepartureEvent.id == departure_id)
            .values(farewell_delivery_id=delivery_id)
        )
    await schedule_rotation_recommendation(session, departure_id=int(departure_id))


async def process_event(session: AsyncSession, event: InboxEvent) -> None:
    # The owner-facing setup bot remains on the Django control plane during the
    # parallel migration. Its durable events are retained and the cutover MR
    # will attach the control-plane consumer before routing that webhook here.
    if event.bot_id is None:
        raise RuntimeError("interface consumer is not enabled before cutover")

    # Telegram can deliver many update types that Welcome does not consume
    # (polls, reactions, edited posts, and others). Completing those events is
    # enough: loading the bot and writing one EventLog row per ignored update
    # turns harmless traffic into avoidable database pressure. Keep the raw
    # inbox payload for retention/debugging and only enter the business path
    # for update shapes that can change Welcome state.
    payload = event.payload
    if not is_actionable_payload(payload):
        return

    bot = await session.get(ManagedBot, event.bot_id)
    if bot is None or not bot.is_active:
        return
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
        now = datetime.now(UTC)
        message_window_expires_at, approval_deadline = message_window(
            join.get("date"),
            now=now,
        )
        due_at = min(
            now + timedelta(seconds=bot.approval_delay_seconds),
            approval_deadline,
        ) if bot.auto_approve else None
        request_id = await session.scalar(
            _join_request_upsert(
                bot=bot,
                channel_id=channel_id,
                contact_id=contact_id,
                update_id=event.update_id,
                user_chat_id=(
                    int(join["user_chat_id"])
                    if join.get("user_chat_id") is not None
                    else None
                ),
                message_window_expires_at=message_window_expires_at,
                due_at=due_at,
                now=now,
            )
        )
        if (
            join.get("user_chat_id") is not None
            and await _join_request_greetings_enabled(session, bot.id)
        ):
            delivery_id = await schedule_content_flow(
                session,
                bot=bot,
                channel_id=channel_id,
                contact_id=contact_id,
                event_key=f"join-request:{event.update_id}",
                target_chat_id=int(join["user_chat_id"]),
                target_expires_at=approval_deadline,
                max_timeline_seconds=JOIN_REQUEST_MAX_TIMELINE_SECONDS,
            )
            if delivery_id is not None:
                await session.execute(
                    update(JoinRequest)
                    .where(JoinRequest.id == request_id)
                    .values(welcome_delivery_id=delivery_id)
                )
            else:
                session.add(
                    EventLog(
                        bot_id=bot.id,
                        owner_id=bot.owner_id,
                        event_type="join_request_greeting_skipped",
                        level="warning",
                        message="No compatible published welcome flow was scheduled",
                        context={"join_request_id": int(request_id)},
                    )
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
        joined, left = await _membership_transition(
            session,
            channel_id=channel_id,
            contact_id=contact_id,
            update_id=event.update_id,
            old_active=old_status in active,
            new_active=new_status in active,
        )
        if joined:
            await close_open_join_request(
                session,
                channel_id=channel_id,
                contact_id=contact_id,
            )
            raw_invite = member.get("invite_link")
            invite_link = str(raw_invite.get("invite_link") or "") if isinstance(raw_invite, dict) else ""
            if invite_link:
                await record_rotation_conversion(
                    session,
                    destination_channel_id=channel_id,
                    telegram_user_id=int(user["id"]),
                    telegram_update_id=event.update_id,
                    invite_link=invite_link,
                )
            await _schedule_greeting(session, bot, channel_id, contact_id, f"chat-member:{event.update_id}")
        elif left:
            await _schedule_departure(
                session,
                event=event,
                bot=bot,
                channel_id=channel_id,
                contact_id=contact_id,
                reason="kicked" if new_status == "kicked" else "left",
            )
        return
