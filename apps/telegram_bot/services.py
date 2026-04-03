"""
Bot-level service helpers — things that belong to the Telegram layer
but are shared across multiple handlers (e.g. safe_edit, safe_send).
"""
from __future__ import annotations
import logging
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


async def safe_edit_text(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """
    Edit message text, silently ignoring 'message is not modified' errors.
    Always answer the callback to remove the loading indicator.
    """
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            pass  # harmless
        else:
            logger.warning("safe_edit_text failed: %s", exc)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass


async def answer_and_edit(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    alert: str | None = None,
) -> None:
    """Answer callback (optionally with alert), then edit message."""
    await callback.answer(alert, show_alert=bool(alert))
    await safe_edit_text(callback, text, reply_markup)
