"""
Channel subscription gate.

All non-admin users must be subscribed to SUBSCRIPTION_CHANNEL_ID to use the bot.
Implemented as an aiogram outer middleware — registered once, covers every
message and callback_query automatically.

Registration order in bot.py (FIFO — first registered runs first):
    dp.message.outer_middleware(UserMiddleware())            ← registered first → runs first
    dp.message.outer_middleware(SubscriptionMiddleware())   ← registered second → runs second

So the actual call order is:
    UserMiddleware → sets db_user in data
    SubscriptionMiddleware → reads db_user, checks channel membership
    Filters + Handler

"Check subscription" button flow:
    User clicks "Проверить подписку" →
        SubscriptionMiddleware runs again:
            not subscribed → blocks, sends gate message (same as before)
            subscribed     → lets through to cb_check_subscription handler
        cb_check_subscription → shows appropriate main menu for the user's role

Error/edge-case policy:
  - SUBSCRIPTION_CHANNEL_ID not set   → check skipped (backward compat)
  - Telegram API error                → fail-open: user allowed, warning logged
  - Bot not admin in channel          → fail-open: user allowed, error logged
  - Member status "restricted"        → treated as subscribed (still a member)
  - Member status "left" / "kicked"   → blocked, shown join button
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Router, F
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from apps.telegram_bot.callbacks import SubscriptionCallback

logger = logging.getLogger(__name__)

router = Router(name="subscription")

# Statuses that mean the user IS a channel member
_MEMBER_STATUSES = frozenset({"creator", "administrator", "member", "restricted"})

# Statuses that mean the user is NOT a member (or was removed)
_NON_MEMBER_STATUSES = frozenset({"left", "kicked"})


async def check_channel_membership(bot, channel_id: int | str, user_id: int) -> bool | None:
    """
    Ask Telegram whether `user_id` is a member of `channel_id`.

    Returns:
        True  — subscribed (status in member/administrator/creator/restricted)
        False — not subscribed (status is left or kicked)
        None  — check failed due to API error; caller decides safe behavior
    """
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        status = member.status
        if status in _MEMBER_STATUSES:
            return True
        if status in _NON_MEMBER_STATUSES:
            return False
        logger.warning(
            "check_channel_membership: unknown status %r for user %d — treating as not subscribed",
            status,
            user_id,
        )
        return False
    except TelegramAPIError as exc:
        logger.error(
            "check_channel_membership: TelegramAPIError for user %d in channel %s: %s",
            user_id,
            channel_id,
            exc,
        )
        return None
    except Exception as exc:
        logger.error(
            "check_channel_membership: unexpected error for user %d: %s",
            user_id,
            exc,
        )
        return None


def _build_gate_keyboard(channel_url: str) -> InlineKeyboardMarkup:
    """Two-button keyboard: subscribe link + check subscription callback."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url=channel_url)],
            [InlineKeyboardButton(
                text="✅ Проверить подписку",
                callback_data=SubscriptionCallback(action="check").pack(),
            )],
        ]
    )


_NOT_SUBSCRIBED_TEXT = (
    "🔒 <b>Доступ ограничен</b>\n\n"
    "Для использования бота необходимо подписаться на наш канал.\n\n"
    "После подписки нажмите кнопку <b>«Проверить подписку»</b>."
)


class SubscriptionMiddleware(BaseMiddleware):
    """
    Outer middleware that enforces channel subscription for all non-admin users.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from django.conf import settings

        channel_id_raw: str = getattr(settings, "SUBSCRIPTION_CHANNEL_ID", "")
        channel_url: str = getattr(
            settings,
            "SUBSCRIPTION_CHANNEL_URL",
            "https://t.me/gramlyspam",
        )

        # ── Skip check if not configured ─────────────────────────────────────
        if not channel_id_raw:
            return await handler(event, data)

        try:
            channel_id: int | str = int(channel_id_raw)
        except (ValueError, TypeError):
            channel_id = channel_id_raw  # e.g. "@grmly"

        # ── Get db_user (set by UserMiddleware which ran before us) ───────────
        db_user = data.get("db_user")
        if db_user is None:
            # System updates (channel posts, etc.) — no user context, allow
            return await handler(event, data)

        # ── Admins always bypass the gate ─────────────────────────────────────
        from apps.users.models import UserRole

        if db_user.role == UserRole.ADMIN:
            return await handler(event, data)

        # ── Check Telegram membership ─────────────────────────────────────────
        bot = data.get("bot")
        if bot is None:
            logger.error("SubscriptionMiddleware: bot not found in data — skipping check (fail-open)")
            return await handler(event, data)

        result = await check_channel_membership(bot, channel_id, db_user.telegram_id)

        if result is None:
            # API error → fail-open
            logger.warning(
                "SubscriptionMiddleware: membership check failed for user %d "
                "(API error) — allowing access (fail-open policy)",
                db_user.telegram_id,
            )
            return await handler(event, data)

        if result is True:
            return await handler(event, data)

        # ── Not subscribed — block and prompt to join ─────────────────────────
        keyboard = _build_gate_keyboard(channel_url)

        if isinstance(event, CallbackQuery):
            await event.answer()
            try:
                await event.message.answer(_NOT_SUBSCRIBED_TEXT, reply_markup=keyboard)
            except Exception:
                pass
        elif isinstance(event, Message):
            await event.answer(_NOT_SUBSCRIBED_TEXT, reply_markup=keyboard)

        return  # block handler


# ── "Check subscription" handler ──────────────────────────────────────────────
#
# When user taps "✅ Проверить подписку":
#   - SubscriptionMiddleware runs first (as always)
#   - If still not subscribed → middleware blocks, sends gate message (no handler called)
#   - If now subscribed → middleware lets through → this handler shows main menu

@router.callback_query(SubscriptionCallback.filter(F.action == "check"))
async def cb_check_subscription(callback: CallbackQuery, db_user, state) -> None:
    """
    Reached only when SubscriptionMiddleware confirmed the user IS now subscribed.
    Show the appropriate main menu for their role.
    """
    await callback.answer("✅ Подписка подтверждена!")

    from asgiref.sync import sync_to_async
    from apps.users.services import UserService

    # Re-fetch to get fresh role/activation state
    db_user = await sync_to_async(UserService.get_by_telegram_id)(db_user.telegram_id) or db_user

    if db_user.is_admin():
        from apps.telegram_bot.handlers.admin.menu import send_admin_main_menu
        await send_admin_main_menu(callback, db_user)
        return

    if db_user.is_curator():
        from apps.telegram_bot.handlers.curator.menu import send_curator_main_menu
        await send_curator_main_menu(callback, db_user)
        return

    # Worker — show main menu or join request flow depending on activation status
    if db_user.is_activated:
        from django.conf import settings
        from apps.telegram_bot.keyboards import get_main_menu_keyboard
        channels_url = getattr(settings, "CHANNELS_DB_URL", "")
        await callback.message.answer(
            f"👋 С возвращением, <b>{db_user.display_name}</b>!\n\nВыберите действие:",
            reply_markup=get_main_menu_keyboard(channels_db_url=channels_url),
        )
    else:
        from apps.telegram_bot.handlers.worker.start import _show_not_activated
        await _show_not_activated(callback, db_user)
