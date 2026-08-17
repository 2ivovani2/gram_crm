from __future__ import annotations

import logging

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types import Update
from asgiref.sync import sync_to_async
from django.utils import timezone

from .models import Channel, Contact, EventLog, ManagedBot, ProcessedUpdate
from .services import contact_from_user, create_join_request, schedule_greeting

logger = logging.getLogger(__name__)
ACTIVE_BOT_STATUSES = {"administrator", "creator"}
MEMBER_STATUSES = {"member", "administrator", "creator", "restricted"}


async def process_customer_update(bot: ManagedBot, update: Update) -> None:
    fresh = await sync_to_async(_claim_update)(bot, update.update_id)
    if not fresh:
        return
    try:
        if update.my_chat_member:
            await _handle_bot_membership(bot, update.my_chat_member)
        elif update.chat_join_request:
            await _handle_join_request(bot, update)
        elif update.chat_member:
            await _handle_member_change(bot, update)
        elif update.message and update.message.chat.type == "private" and update.message.from_user:
            await _handle_private_message(bot, update)
    except Exception:
        # A Celery retry must be able to claim the same Telegram update again.
        await sync_to_async(ProcessedUpdate.objects.filter(bot=bot, update_id=update.update_id).delete)()
        raise


def _claim_update(bot: ManagedBot, update_id: int) -> bool:
    _, created = ProcessedUpdate.objects.get_or_create(bot=bot, update_id=update_id)
    return created


async def _handle_bot_membership(bot: ManagedBot, event) -> None:
    if event.chat.type == "private":
        status = str(event.new_chat_member.status)
        if status in {"kicked", "left"}:
            await sync_to_async(Contact.objects.filter(bot=bot, telegram_id=event.chat.id).update)(
                delivery_status=Contact.DeliveryStatus.DEAD,
                last_delivery_at=timezone.now(),
                last_error="Пользователь заблокировал бота",
            )
        elif status in {"member", "administrator"}:
            await sync_to_async(Contact.objects.filter(bot=bot, telegram_id=event.chat.id).update)(
                delivery_status=Contact.DeliveryStatus.LIVE,
                last_delivery_at=timezone.now(),
                last_error="",
                bot_started=True,
            )
        return
    if event.chat.type not in {"channel", "supergroup"}:
        return
    status = str(event.new_chat_member.status)
    active = status in ACTIVE_BOT_STATUSES
    channel, created = await sync_to_async(Channel.objects.update_or_create)(
        bot=bot,
        telegram_id=event.chat.id,
        defaults={
            "title": event.chat.title or str(event.chat.id),
            "username": event.chat.username or "",
            "is_active": active,
            "can_invite_users": bool(getattr(event.new_chat_member, "can_invite_users", False)),
            "disconnected_at": None if active else timezone.now(),
        },
    )
    event_type = "channel_connected" if active else "channel_disconnected"
    await sync_to_async(EventLog.objects.create)(
        bot=bot,
        owner=bot.owner,
        event_type=event_type,
        message=f"{'Подключён' if active else 'Отключён'} канал «{channel.title}»",
        context={"chat_id": channel.telegram_id},
    )
    await _notify_owner(
        bot.owner.telegram_id,
        f"{'✅' if active else '⚠️'} Бот @{bot.username} "
        f"{'подключён к' if active else 'больше не обслуживает'} каналу «{channel.title}»."
        + ("\n\nОсталось настроить приветственное сообщение." if active else ""),
        reply_markup=(
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Настроить приветствие", callback_data=f"msg:{bot.id}")],
                [InlineKeyboardButton(text="⚙️ Открыть карточку", callback_data=f"bot:{bot.id}")],
            ])
            if active else None
        ),
    )


async def _handle_join_request(bot: ManagedBot, update: Update) -> None:
    event = update.chat_join_request
    channel = await sync_to_async(_active_channel)(bot, event.chat.id, event.chat.title or str(event.chat.id), event.chat.username or "")
    contact = await sync_to_async(contact_from_user)(bot, event.from_user)
    await sync_to_async(create_join_request)(bot, channel, contact, update.update_id)


async def _handle_member_change(bot: ManagedBot, update: Update) -> None:
    event = update.chat_member
    old_status = str(event.old_chat_member.status)
    new_status = str(event.new_chat_member.status)
    user = event.new_chat_member.user
    channel = await sync_to_async(_active_channel)(bot, event.chat.id, event.chat.title or str(event.chat.id), event.chat.username or "")
    contact = await sync_to_async(contact_from_user)(bot, user)
    if old_status not in MEMBER_STATUSES and new_status in MEMBER_STATUSES:
        await sync_to_async(schedule_greeting)(bot, channel, contact, f"chat-member:{update.update_id}")


async def _handle_private_message(bot: ManagedBot, update: Update) -> None:
    message = update.message
    contact = await sync_to_async(contact_from_user)(bot, message.from_user, bot_started=True)
    # A successful inbound message proves that the dialog exists. It does not
    # prove a future outbound delivery, so UNKNOWN is preserved until sending.
    if message.text and message.text.startswith("/start"):
        from .telegram_api import make_bot

        customer_bot = make_bot(bot.get_token(), html=True)
        try:
            await customer_bot.send_message(
                message.chat.id,
                "👋 Отлично, теперь я смогу присылать вам приветствия после вступления в канал.",
            )
            await sync_to_async(Contact.objects.filter(pk=contact.pk).update)(
                delivery_status=Contact.DeliveryStatus.LIVE,
                last_delivery_at=timezone.now(),
                last_error="",
            )
        finally:
            await customer_bot.session.close()


def _active_channel(bot: ManagedBot, chat_id: int, title: str, username: str) -> Channel:
    channel, _ = Channel.objects.get_or_create(
        bot=bot,
        telegram_id=chat_id,
        defaults={"title": title, "username": username, "is_active": True},
    )
    if not channel.is_active:
        channel.is_active = True
        channel.disconnected_at = None
        channel.save(update_fields=("is_active", "disconnected_at"))
    return channel


async def _notify_owner(chat_id: int, text: str, reply_markup=None) -> None:
    from django.conf import settings

    if not settings.WELCOME_BOT_TOKEN:
        return
    from .interface import get_interface_bot

    try:
        await get_interface_bot().send_message(chat_id, text, reply_markup=reply_markup)
    except Exception:
        logger.exception("Could not notify welcome-bot owner %s", chat_id)
