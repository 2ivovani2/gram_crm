from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy.ext.asyncio import AsyncSession

from .models import FeatureFlag

# A restrained, static icon system for the owner bot. Product icons come from
# AppleIcons26; destructive/system icons come from WindowsIcons. Both packs
# were verified through getStickerSet: every selected sticker is static.
DEFAULT_PREMIUM_EMOJI: dict[str, str] = {
    "home": "5222048081469540579",
    "bot": "5222034552322555814",
    "channel": "5222223195876135675",
    "message": "5222151809224705882",
    "subscription": "5221994849644870178",
    "analytics": "5222129067372874693",
    "referral": "5827865545126448742",
    "help": "5222179494583894918",
    "guide": "5221944735966460787",
    "settings": "5224533196791645119",
    "add": "5222166223134948810",
    "edit": "5224638956066340457",
    "preview": "5222464169311241162",
    "publish": "5221929261199296089",
    "media": "5222289441451705159",
    "timer": "5222059742305747580",
    "success": "5224380798467080414",
    "warning": "5938188982784363983",
    "error": "5936119293878996291",
    "delete": "5936274861889424782",
    "back": "5935952739342224399",
    "next": "5936069167315684574",
    "copy": "5938090945860865210",
    "rotation": "5222301445885298017",
    "requests": "5224187211406151308",
    "important": "5221929261199296089",
}

FALLBACK_EMOJI: dict[str, str] = {
    "home": "🏠",
    "bot": "🤖",
    "channel": "📡",
    "message": "💬",
    "subscription": "💳",
    "analytics": "📊",
    "referral": "🔗",
    "help": "💡",
    "guide": "📚",
    "settings": "⚙️",
    "add": "➕",
    "edit": "📝",
    "preview": "🔎",
    "publish": "🎯",
    "media": "📷",
    "timer": "🕒",
    "success": "✅",
    "warning": "⚠️",
    "error": "❌",
    "delete": "🗑",
    "back": "🏠",
    "next": "⤵️",
    "copy": "📋",
    "rotation": "🌐",
    "requests": "✉️",
    "important": "🎯",
}

_EMOJI_RE = re.compile(
    r"(?:[\U0001F000-\U0001FAFF\u2600-\u27BF\U0001F1E6-\U0001F1FF]"
    r"(?:\uFE0F|\u200D[\U0001F000-\U0001FAFF\u2600-\u27BF])*)\s*"
)
_TG_EMOJI_RE = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>')


@dataclass(frozen=True)
class BotUiTheme:
    premium_emoji: dict[str, str] = field(default_factory=dict)
    enhanced: bool = False


async def load_bot_ui_theme(session: AsyncSession) -> BotUiTheme:
    flag = await session.get(FeatureFlag, "bot_inline_ui")
    if flag is None or not flag.enabled:
        return BotUiTheme(premium_emoji=DEFAULT_PREMIUM_EMOJI, enhanced=True)
    raw = flag.config.get("premium_emoji", {}) if isinstance(flag.config, dict) else {}
    emoji = {
        str(key): str(value)
        for key, value in raw.items()
        if str(value).isdigit() and 1 <= len(str(value)) <= 32
    }
    return BotUiTheme(premium_emoji={**DEFAULT_PREMIUM_EMOJI, **emoji}, enhanced=True)


def infer_emoji_key(text: str, callback_data: str | None = None, url: str | None = None) -> str:
    value = f"{callback_data or ''} {text}".lower()
    if url:
        return "next"
    rules = (
        (("delete", "удал", "отключ"), "delete"),
        (("cancel", "отмена", "нет"), "error"),
        (("help", "помощ", "совет", "инструк"), "help"),
        (("analytics", "stats", "статист", "аналит"), "analytics"),
        (("subscription", "pay:", "подпис", "оплат"), "subscription"),
        (("referral", "партн"), "referral"),
        (("rotation", "ротац"), "rotation"),
        (("request", "заяв"), "requests"),
        (("channel", "connect", "канал"), "channel"),
        (("preview", "предпросмотр"), "preview"),
        (("publish", "опубликов", "начать работу"), "publish"),
        (("copy", "копи"), "copy"),
        (("delay", "first:", "сек", "мин", "час", "задерж"), "timer"),
        (("add", "создать", "добавить"), "add"),
        (("message", "msg:", "chain:", "сообщ", "цепоч"), "message"),
        (("bot:", "bots:", "бот"), "bot"),
        (("back", "назад", "главное меню", "к боту", "←", "⬅"), "back"),
        (("next", "далее", "вперёд", "➡", "→"), "next"),
        (("ready", "готов", "confirm", "да", "проверить"), "success"),
        (("settings", "настро"), "settings"),
    )
    for needles, key in rules:
        if any(needle in value for needle in needles):
            return key
    return "important"


def premium_text(text: str, emoji_key: str, theme: BotUiTheme | None = None) -> str:
    active_theme = theme or BotUiTheme(premium_emoji=DEFAULT_PREMIUM_EMOJI, enhanced=True)
    fallback = FALLBACK_EMOJI.get(emoji_key, FALLBACK_EMOJI["important"])
    clean = _EMOJI_RE.sub("", text)
    custom_id = active_theme.premium_emoji.get(emoji_key)
    if active_theme.enhanced and custom_id:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji> {clean}'
    return f"{fallback} {clean}"


def plain_text(text: str) -> str:
    return _TG_EMOJI_RE.sub(r"\1", text)


def inline_button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    web_app: WebAppInfo | None = None,
    style: str | None = None,
    emoji_key: str | None = None,
    theme: BotUiTheme | None = None,
) -> InlineKeyboardButton:
    emoji_key = emoji_key or infer_emoji_key(text, callback_data, url)
    values: dict[str, Any] = {"text": _EMOJI_RE.sub("", text)}
    if callback_data is not None:
        values["callback_data"] = callback_data
    if url is not None:
        values["url"] = url
    if web_app is not None:
        values["web_app"] = web_app
    fields = InlineKeyboardButton.model_fields
    theme = theme or BotUiTheme(premium_emoji=DEFAULT_PREMIUM_EMOJI, enhanced=True)
    if theme.enhanced:
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
    if "<tg-emoji" not in text:
        text = premium_text(text, infer_emoji_key(text))
    try:
        await message.answer(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        fallback_text = plain_text(text)
        fallback_markup = plain_markup(reply_markup)
        if fallback_text == text and fallback_markup == reply_markup:
            raise
        await message.answer(fallback_text, reply_markup=fallback_markup)


async def edit_with_ui_fallback(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if "<tg-emoji" not in text:
        text = premium_text(text, infer_emoji_key(text))
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        fallback_text = plain_text(text)
        fallback_markup = plain_markup(reply_markup)
        if fallback_text == text and fallback_markup == reply_markup:
            raise
        await message.edit_text(fallback_text, reply_markup=fallback_markup)


async def edit_caption_with_ui_fallback(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if "<tg-emoji" not in text:
        text = premium_text(text, infer_emoji_key(text))
    try:
        await message.edit_caption(caption=text, reply_markup=reply_markup)
    except TelegramBadRequest:
        fallback_text = plain_text(text)
        fallback_markup = plain_markup(reply_markup)
        if fallback_text == text and fallback_markup == reply_markup:
            raise
        await message.edit_caption(caption=fallback_text, reply_markup=fallback_markup)
