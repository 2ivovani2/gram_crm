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

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

logger = logging.getLogger(__name__)

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

    Errors are logged but not re-raised so a bad bot configuration
    never bricks the entire bot for all users.
    """
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        status = member.status
        if status in _MEMBER_STATUSES:
            return True
        if status in _NON_MEMBER_STATUSES:
            return False
        # Unknown future status → treat as not subscribed (safe default)
        logger.warning(
            "check_channel_membership: unknown status %r for user %d — treating as not subscribed",
            status,
            user_id,
        )
        return False
    except TelegramAPIError as exc:
        # Common causes:
        #   - Bot is not a member / not an admin of the channel
        #   - User_id not found
        #   - Telegram rate limit
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


def _build_join_keyboard(channel_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url=channel_url)]
        ]
    )


_NOT_SUBSCRIBED_TEXT = (
    "🔒 <b>Доступ ограничен</b>\n\n"
    "Для использования бота необходимо подписаться на наш канал.\n\n"
    "После подписки просто повторите своё действие."
)


class SubscriptionMiddleware(BaseMiddleware):
    """
    Outer middleware that enforces channel subscription for all non-admin users.

    Must be registered BEFORE UserMiddleware in bot.py so that in the
    LIFO middleware chain it executes AFTER UserMiddleware
    (and therefore has `db_user` already set in `data`).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from django.conf import settings

        db_user_debug = data.get("db_user")
        logger.info(
            "SubscriptionMiddleware.__call__ invoked: event=%s db_user=%s",
            type(event).__name__, db_user_debug,
        )

        channel_id_raw: str = getattr(settings, "SUBSCRIPTION_CHANNEL_ID", "")
        channel_url: str = getattr(
            settings,
            "SUBSCRIPTION_CHANNEL_URL",
            "https://t.me/+srQfQzCb_6gyY2Rh",
        )

        # ── Skip check if not configured ─────────────────────────────────────
        if not channel_id_raw:
            return await handler(event, data)

        # Support both public channels (@username) and private channels (-100XXXXXXXXX).
        # Try to parse as int; if it fails, use the string as-is (@username).
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
            # Should never happen; fail-open so bot doesn't brick
            logger.error("SubscriptionMiddleware: bot not found in data — skipping check (fail-open)")
            return await handler(event, data)

        result = await check_channel_membership(bot, channel_id, db_user.telegram_id)
        logger.info(
            "SubscriptionMiddleware: user=%d channel=%s result=%r event_type=%s",
            db_user.telegram_id, channel_id, result, type(event).__name__,
        )

        if result is None:
            # API error → fail-open: allow the user, but log loudly
            logger.warning(
                "SubscriptionMiddleware: membership check failed for user %d "
                "(API error) — allowing access (fail-open policy)",
                db_user.telegram_id,
            )
            return await handler(event, data)

        if result is True:
            # Subscribed — proceed normally
            return await handler(event, data)

        # ── Not subscribed — block and prompt to join ─────────────────────────
        keyboard = _build_join_keyboard(channel_url)

        if isinstance(event, CallbackQuery):
            # Must answer() to dismiss the loading spinner on the button
            await event.answer()
            try:
                await event.message.answer(_NOT_SUBSCRIBED_TEXT, reply_markup=keyboard)
            except Exception:
                # Edge case: no message context (inline keyboard in other chats, etc.)
                pass
        elif isinstance(event, Message):
            await event.answer(_NOT_SUBSCRIBED_TEXT, reply_markup=keyboard)

        # Do NOT call handler — action is blocked
        return
