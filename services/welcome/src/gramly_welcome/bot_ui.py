from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from .models import FeatureFlag


@dataclass(frozen=True)
class BotUiTheme:
    premium_emoji: dict[str, str] = field(default_factory=dict)
    enhanced: bool = False


async def load_bot_ui_theme(session: AsyncSession) -> BotUiTheme:
    flag = await session.get(FeatureFlag, "bot_inline_ui")
    if flag is None or not flag.enabled:
        return BotUiTheme()
    raw = flag.config.get("premium_emoji", {}) if isinstance(flag.config, dict) else {}
    emoji = {
        str(key): str(value)
        for key, value in raw.items()
        if str(value).isdigit() and 1 <= len(str(value)) <= 32
    }
    return BotUiTheme(premium_emoji=emoji, enhanced=True)


def inline_button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
    emoji_key: str | None = None,
    theme: BotUiTheme | None = None,
) -> InlineKeyboardButton:
    values: dict[str, Any] = {"text": text}
    if callback_data is not None:
        values["callback_data"] = callback_data
    if url is not None:
        values["url"] = url
    fields = InlineKeyboardButton.model_fields
    if theme is not None and theme.enhanced:
        if style in {"primary", "success", "danger"} and "style" in fields:
            values["style"] = style
        custom_emoji_id = theme.premium_emoji.get(emoji_key or "")
        if custom_emoji_id and "icon_custom_emoji_id" in fields:
            values["icon_custom_emoji_id"] = custom_emoji_id
    return InlineKeyboardButton(**values)


def plain_markup(markup: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    if markup is None:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for row in markup.inline_keyboard:
        plain_row: list[InlineKeyboardButton] = []
        for button in row:
            values = button.model_dump(exclude_none=True)
            values.pop("style", None)
            values.pop("icon_custom_emoji_id", None)
            plain_row.append(InlineKeyboardButton(**values))
        rows.append(plain_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def answer_with_ui_fallback(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await message.answer(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        if reply_markup is None:
            raise
        await message.answer(text, reply_markup=plain_markup(reply_markup))


async def edit_with_ui_fallback(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        if reply_markup is None:
            raise
        await message.edit_text(text, reply_markup=plain_markup(reply_markup))
