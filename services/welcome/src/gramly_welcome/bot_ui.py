from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy.ext.asyncio import AsyncSession

from .models import FeatureFlag

# A restrained, static icon system from the Telegram-native IconsInTg set.
# The selected stickers were verified through getStickerSet. Keeping one set
# gives the owner bot a consistent product UI without Apple-style artwork.
DEFAULT_PREMIUM_EMOJI: dict[str, str] = {
    "home": "5974453749601537448",
    "bot": "5971808079811972376",
    "channel": "5783105032350076195",
    "message": "5974490089319828950",
    "subscription": "5976377521287990495",
    "analytics": "5974047364090957805",
    "referral": "5974492756494519709",
    "help": "6001517450930163276",
    "guide": "5974290527959386992",
    "settings": "5974104203688152439",
    "add": "5971860323794160759",
    "edit": "6010548023396928773",
    "preview": "5974350313904147369",
    "publish": "5974192980662160632",
    "media": "5974563790958627920",
    "timer": "5974585609392492550",
    "success": "6008275560495582704",
    "warning": "5976801477509778431",
    "error": "5972201876773408053",
    "delete": "5974518878485615140",
    "back": "5854967531793550989",
    "next": "5974249837439224721",
    "copy": "5974434516737985904",
    "rotation": "6010590938710152619",
    "requests": "5775973900580031963",
    "important": "5972187557352443077",
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
