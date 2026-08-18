from __future__ import annotations

import io
import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import PurePath
from typing import Any, cast

import redis.asyncio as redis
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
    Update,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.exc import IntegrityError

from .config import Settings, get_settings
from .crypto import TokenKeyring
from .db import session_factory
from .models import ManagedBot, Owner
from .owner_repository import (
    append_album_item,
    bot_channels,
    bot_media_keys,
    bot_statistics,
    create_managed_bot,
    delete_bot,
    list_owned_bots,
    mark_guide_complete,
    owned_bot,
    owner_from_telegram,
    pending_requests,
    save_welcome_message,
    set_webhook_configured,
    toggle_auto_approve,
    update_delay,
)
from .storage import MediaTooLargeError, ObjectStorage

logger = logging.getLogger(__name__)
router = Router(name="gramly-welcome-owner")
BOTS_PER_PAGE = 6
SUPPORTED_CONTENT = {
    "text",
    "animation",
    "audio",
    "contact",
    "dice",
    "document",
    "location",
    "photo",
    "poll",
    "sticker",
    "venue",
    "video",
    "video_note",
    "voice",
}
DOWNLOADABLE_TYPES = {
    "animation",
    "audio",
    "document",
    "photo",
    "sticker",
    "video",
    "video_note",
    "voice",
}

GUIDE = [
    (
        "✨ <b>Всё управление — в одном месте</b>",
        "Подключайте собственных ботов, управляйте каналами, приветствиями и заявками прямо в Telegram.",
    ),
    (
        "🤖 <b>Подключение за три шага</b>",
        "Создайте бота в @BotFather → пришлите токен → добавьте бота администратором в канал.",
    ),
    (
        "💬 <b>Приветственные сообщения</b>",
        "Сохраняйте текст, форматирование и медиа и задавайте задержку отправки.",
    ),
    (
        "📥 <b>Заявки на вступление</b>",
        "Включите автоматическое принятие и настройте независимую задержку.",
    ),
    (
        "📊 <b>Актуальная статистика</b>",
        "Следите за доставкой, языками и аудиторией каждого подключённого бота.",
    ),
    (
        "🚀 <b>Можно начинать</b>",
        "Подключите первого бота — остальную рутину Gramly Welcome возьмёт на себя.",
    ),
]


def _callback_message(callback: CallbackQuery) -> Message:
    return cast(Message, callback.message)


class AddBotState(StatesGroup):
    waiting_for_token = State()


class WelcomeMessageState(StatesGroup):
    waiting_for_message = State()


class OwnerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user if isinstance(event, (Message, CallbackQuery)) else None
        if user is None:
            return None
        async with session_factory() as session:
            data["owner"] = await owner_from_telegram(session, user)
        return await handler(event, data)


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🤖 Мои Боты")],
            [KeyboardButton(text="💎 ПОДПИСКА"), KeyboardButton(text="📈 Аналитика")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def _one_button(text: str, callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback)]]
    )


def _add_to_channel_url(bot: ManagedBot) -> str:
    return f"https://t.me/{bot.username}?startchannel&admin=invite_users+manage_chat"


def _connection_keyboard(bot: ManagedBot) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"➕ Добавить @{bot.username} в канал",
                    url=_add_to_channel_url(bot),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Проверить подключение",
                    callback_data=f"connect-check:{bot.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Открыть карточку бота", callback_data=f"bot:{bot.id}"
                )
            ],
        ]
    )


def _format_delay(seconds: int) -> str:
    if not seconds:
        return "сразу"
    parts = []
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if seconds:
        parts.append(f"{seconds} сек")
    return " ".join(parts)


def _entity_dump(entities: list[Any] | None) -> list[dict[str, Any]]:
    return [entity.model_dump(mode="json", exclude_none=True) for entity in (entities or [])]


def serialize_message(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": message.content_type,
        "text": message.text,
        "caption": message.caption,
        "entities": _entity_dump(message.entities),
        "caption_entities": _entity_dump(message.caption_entities),
        "has_spoiler": bool(message.has_media_spoiler),
    }
    for field in ("location", "contact", "venue", "dice", "poll"):
        value = getattr(message, field, None)
        if value is not None:
            payload[field] = value.model_dump(mode="json", exclude_none=True)
    return {key: value for key, value in payload.items() if value not in (None, [], {})}


def _downloadable(message: Message) -> tuple[str, Any, str, str] | None:
    kind = message.content_type
    if kind == "photo" and message.photo:
        return kind, message.photo[-1], "photo.jpg", "image/jpeg"
    obj = getattr(message, kind, None) if kind in DOWNLOADABLE_TYPES else None
    if obj is None:
        return None
    name = PurePath(getattr(obj, "file_name", "") or f"{kind}.bin").name
    mime = getattr(obj, "mime_type", "") or "application/octet-stream"
    return kind, obj, name, mime


async def download_message_media(
    bot: Bot, message: Message, storage: ObjectStorage, prefix: str
) -> dict[str, Any] | None:
    item = _downloadable(message)
    if item is None:
        return None
    kind, telegram_file, filename, mime = item
    declared = int(getattr(telegram_file, "file_size", 0) or 0)
    if declared > storage.max_bytes:
        raise MediaTooLargeError("Файл превышает лимит Telegram-бота 20 МБ.")
    target = io.BytesIO()
    await bot.download(telegram_file, destination=target)
    body = target.getvalue()
    key = f"{prefix}/{uuid.uuid4().hex}-{filename}"
    await storage.upload(key, body, mime)
    return {
        "media_type": kind,
        "storage_key": key,
        "original_name": filename,
        "mime_type": mime,
        "size": len(body),
    }


async def configure_customer_webhook(
    managed: ManagedBot, token: str, settings: Settings
) -> None:
    url = (
        f"{settings.public_webhook_base_url.rstrip('/')}/"
        f"{managed.public_id}/{managed.path_secret}/"
    )
    async with Bot(token=token) as bot:
        await bot.set_webhook(
            url,
            secret_token=managed.webhook_secret,
            allowed_updates=["message", "my_chat_member", "chat_member", "chat_join_request"],
            drop_pending_updates=False,
            max_connections=20,
        )


async def remove_customer_webhook(managed: ManagedBot, settings: Settings) -> None:
    token = TokenKeyring.parse(settings.token_encryption_keys).decrypt(
        managed.token_ciphertext
    )
    async with Bot(token=token) as bot:
        await bot.delete_webhook(drop_pending_updates=True)


@router.message(CommandStart())
async def start(message: Message, owner: Owner, state: FSMContext) -> None:
    await state.clear()
    if owner.guide_completed:
        await show_main_menu(message)
        return
    await message.answer(
        "👋 <b>Добро пожаловать в Gramly Welcome!</b>\n\n"
        "Подключайте собственных Telegram-ботов, автоматически принимайте заявки, "
        "отправляйте приветствия и следите за результатом.\n\n"
        "Короткое знакомство займёт меньше минуты.",
        reply_markup=_one_button("➡️ Далее", "guide:0"),
    )


@router.callback_query(F.data.startswith("guide:"))
async def guide(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    step = int((callback.data or "").split(":")[1])
    if step >= len(GUIDE):
        async with session_factory() as session:
            await mark_guide_complete(session, owner.id, len(GUIDE))
        await message.answer(
            "Готово — всё управление уже под рукой ✨", reply_markup=main_keyboard()
        )
        await show_bots(message, owner, 0)
        await callback.answer()
        return
    title, body = GUIDE[step]
    final = step == len(GUIDE) - 1
    markup = _one_button(
        "🚀 Начать работу" if final else "➡️ Далее", f"guide:{step + 1}"
    )
    content = f"{title}\n\n{body}\n\n<i>Шаг {step + 1} из {len(GUIDE)}</i>"
    if message.photo:
        # Keep pre-cutover onboarding messages usable after the owner webhook
        # moves away from Django.
        await message.edit_caption(caption=content, reply_markup=markup)
    else:
        await message.edit_text(content, reply_markup=markup)
    await callback.answer()


async def show_main_menu(message: Message) -> None:
    await message.answer(
        "🏠 <b>Главное меню</b>\n\nВыберите раздел:", reply_markup=main_keyboard()
    )


@router.message(F.text == "🤖 Мои Боты")
async def bots_menu(message: Message, owner: Owner, state: FSMContext) -> None:
    await state.clear()
    await show_bots(message, owner, 0)


async def show_bots(target: Message, owner: Owner, page: int) -> None:
    async with session_factory() as session:
        bots, total = await list_owned_bots(
            session, owner.id, page * BOTS_PER_PAGE, BOTS_PER_PAGE
        )
    page_count = max(1, (total + BOTS_PER_PAGE - 1) // BOTS_PER_PAGE)
    page = max(0, min(page, page_count - 1))
    keyboard = InlineKeyboardBuilder()
    for item in bots:
        keyboard.button(
            text=f"🤖 @{item.username or item.display_name}", callback_data=f"bot:{item.id}"
        )
    keyboard.adjust(1)
    navigation = []
    if page:
        navigation.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"bots:{page - 1}")
        )
    if page + 1 < page_count:
        navigation.append(
            InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"bots:{page + 1}")
        )
    if navigation:
        keyboard.row(*navigation)
    keyboard.row(InlineKeyboardButton(text="➕ Создать Бота", callback_data="bot:add"))
    text = (
        "У Вас пока нет подключенных ботов."
        if not total
        else f"<b>Ваши боты</b> · {total}\n\nВыберите бота для управления:"
    )
    await target.answer(text, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith("bots:"))
async def paginate_bots(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    await message.delete()
    await show_bots(message, owner, int((callback.data or "").split(":")[1]))
    await callback.answer()


@router.callback_query(F.data == "bot:add")
async def add_bot(callback: CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(callback)
    await state.set_state(AddBotState.waiting_for_token)
    await message.answer(
        "🤖 <b>Подключение бота · шаг 1 из 3</b>\n\n"
        "Создайте бота в @BotFather командой <code>/newbot</code>, скопируйте токен "
        "и отправьте его сюда.\n\n🔒 Сообщение с токеном удалится сразу после получения.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🤖 Открыть @BotFather", url="https://t.me/BotFather?start=bot"
                    )
                ],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
            ]
        ),
    )
    await callback.answer()


@router.message(AddBotState.waiting_for_token)
async def receive_token(message: Message, owner: Owner, state: FSMContext) -> None:
    token = (message.text or "").strip()
    try:
        await message.delete()
    except TelegramAPIError:
        pass
    if not token or ":" not in token or len(token) > 200:
        await message.answer("Токен выглядит некорректно. Проверьте его и отправьте ещё раз.")
        return
    managed: ManagedBot | None = None
    settings = get_settings()
    try:
        async with Bot(token=token) as probe:
            info = await probe.get_me()
        async with session_factory() as session:
            managed = await create_managed_bot(session, owner.id, token, info, settings)
        await configure_customer_webhook(managed, token, settings)
        async with session_factory() as session:
            await set_webhook_configured(session, managed.id, True)
    except IntegrityError:
        await message.answer("Этот бот уже подключён к системе другим владельцем.")
        return
    except TelegramAPIError:
        if managed:
            async with session_factory() as session:
                await delete_bot(session, managed.id)
        await message.answer(
            "Не удалось проверить токен или настроить webhook. Проверьте токен и попробуйте ещё раз."
        )
        return
    except Exception:
        logger.exception("Could not register customer bot")
        if managed:
            try:
                await remove_customer_webhook(managed, settings)
            except Exception:
                logger.warning(
                    "Could not roll back customer webhook bot_id=%s", managed.id,
                    exc_info=True,
                )
            async with session_factory() as session:
                await delete_bot(session, managed.id)
        await message.answer("Telegram временно недоступен. Попробуйте чуть позже.")
        return
    await state.clear()
    await message.answer(
        f"✅ <b>Токен принят · шаг 2 из 3</b>\n\n"
        f"Бот <b>@{managed.username}</b> зарегистрирован. Добавьте его администратором "
        "канала с правом «Добавление подписчиков».",
        reply_markup=_connection_keyboard(managed),
    )


async def _get_owned(owner: Owner, raw_id: str) -> ManagedBot | None:
    try:
        bot_id = int(raw_id)
    except ValueError:
        return None
    async with session_factory() as session:
        return await owned_bot(session, owner.id, bot_id)


@router.callback_query(F.data.startswith("connect:"))
async def connection_help(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    bot = await _get_owned(owner, (callback.data or "").split(":")[1])
    if bot is None:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await message.answer(
        f"🔗 <b>Подключение @{bot.username} к каналу</b>\n\n"
        "Добавьте бота администратором и не отключайте право «Добавление подписчиков».",
        reply_markup=_connection_keyboard(bot),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("connect-check:"))
async def connection_check(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    bot = await _get_owned(owner, (callback.data or "").split(":")[1])
    if bot is None:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    async with session_factory() as session:
        channels = await bot_channels(session, bot.id)
    if not channels:
        await callback.answer(
            "Пока не вижу канал. Проверьте права бота и повторите через несколько секунд.",
            show_alert=True,
        )
        return
    names = "\n".join(f"• {channel.title}" for channel in channels)
    await message.answer(
        f"🎉 <b>Подключение завершено · шаг 3 из 3</b>\n\n@{bot.username} обслуживает:\n{names}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💬 Настроить приветствие", callback_data=f"msg:{bot.id}")],
                [InlineKeyboardButton(text="⚙️ Открыть карточку", callback_data=f"bot:{bot.id}")],
            ]
        ),
    )
    await callback.answer("Канал подключён")


@router.callback_query(F.data.startswith("bot:"))
async def bot_card(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    tail = (callback.data or "").split(":", 1)[1]
    if tail == "add":
        return
    bot = await _get_owned(owner, tail)
    if bot is None:
        await callback.answer("Бот не найден или Вам не принадлежит.", show_alert=True)
        return
    await send_bot_card(message, bot)
    await callback.answer()


async def send_bot_card(message: Message, bot: ManagedBot) -> None:
    async with session_factory() as session:
        data = await bot_statistics(session, bot.id)
        channels = await bot_channels(session, bot.id)
    channel_text = (
        "\n".join(f"  • {channel.title}" for channel in channels)
        if channels
        else "  ⚠️ Пока не подключено ни одного канала"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Подключить канал", callback_data=f"connect:{bot.id}")],
            [
                InlineKeyboardButton(text="💬 Сообщения", callback_data=f"msg:{bot.id}"),
                InlineKeyboardButton(text="📥 Заявки", callback_data=f"requests:{bot.id}"),
            ],
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data=f"stats:{bot.id}"),
                InlineKeyboardButton(text="🗑 Удалить бота", callback_data=f"delete:{bot.id}"),
            ],
        ]
    )
    await message.answer(
        f"🤖 <b>@{bot.username or bot.display_name}</b>\n<code>{bot.telegram_id}</code>\n\n"
        f"🟢 Живые: <b>{data['live']}</b>\n🔴 Мёртвые: <b>{data['dead']}</b>\n"
        f"📣 Каналы: <b>{len(channels)}</b>\n{channel_text}",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("msg:"))
async def set_message(callback: CallbackQuery, owner: Owner, state: FSMContext) -> None:
    message = _callback_message(callback)
    bot = await _get_owned(owner, (callback.data or "").split(":")[1])
    if bot is None:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.set_state(WelcomeMessageState.waiting_for_message)
    await state.update_data(bot_id=bot.id)
    await message.answer(
        "💬 <b>Новое приветствие</b>\n\nОтправьте текст или поддерживаемое Telegram-вложение.",
        reply_markup=_one_button("❌ Отмена", f"bot:{bot.id}"),
    )
    await callback.answer()


@router.message(WelcomeMessageState.waiting_for_message)
async def receive_welcome(message: Message, owner: Owner, state: FSMContext) -> None:
    if message.content_type not in SUPPORTED_CONTENT:
        await message.answer("Этот тип сообщения нельзя безопасно воспроизвести.")
        return
    data = await state.get_data()
    bot = await _get_owned(owner, str(data.get("bot_id", "")))
    if bot is None:
        await state.clear()
        await message.answer("Сессия настройки устарела. Откройте бота ещё раз.")
        return
    settings = get_settings()
    storage = ObjectStorage(settings)
    media: dict[str, Any] | None = None
    try:
        media = await download_message_media(
            cast(Bot, message.bot), message, storage, f"welcome-bots/{bot.public_id}/messages"
        )
        payload = serialize_message(message)
        async with session_factory() as session:
            if message.media_group_id:
                if message.content_type not in {"photo", "video", "audio", "document"} or not media:
                    raise ValueError("Этот тип вложения нельзя сохранить внутри Telegram-альбома.")
                await append_album_item(
                    session,
                    bot.id,
                    owner.id,
                    message.media_group_id,
                    message.message_id,
                    payload,
                    media,
                )
                await message.answer("📎 Часть альбома принята, сохраняю композицию…")
                return
            version = await save_welcome_message(
                session, bot.id, owner.id, owner.telegram_id, payload, media
            )
    except (ValueError, MediaTooLargeError) as exc:
        await message.answer(f"⚠️ {exc}")
        return
    except Exception:
        if media:
            await storage.delete_many([str(media["storage_key"])])
        logger.exception("Could not save welcome message bot_id=%s", bot.id)
        await message.answer("Не удалось сохранить сообщение целиком. Попробуйте ещё раз.")
        return
    await state.clear()
    await message.answer(f"✅ Приветствие сохранено · версия {version.version}")
    await send_delay_screen(message, bot, "welcome")


async def send_delay_screen(message: Message, bot: ManagedBot, kind: str) -> None:
    value = bot.welcome_delay_seconds if kind == "welcome" else bot.approval_delay_seconds
    title = "Задержка приветственного сообщения" if kind == "welcome" else "Задержка принятия"
    prefix = "wdelay" if kind == "welcome" else "adelay"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="− 5 мин", callback_data=f"{prefix}:{bot.id}:-300"),
                InlineKeyboardButton(text="+ 5 мин", callback_data=f"{prefix}:{bot.id}:300"),
            ],
            [
                InlineKeyboardButton(text="− 1 час", callback_data=f"{prefix}:{bot.id}:-3600"),
                InlineKeyboardButton(text="+ 1 час", callback_data=f"{prefix}:{bot.id}:3600"),
            ],
            [InlineKeyboardButton(text="Обнулить", callback_data=f"{prefix}:{bot.id}:zero")],
            [InlineKeyboardButton(text="✅ Готово", callback_data=f"bot:{bot.id}")],
        ]
    )
    await message.answer(
        f"⏱ <b>{title}</b>\n\nТекущее значение: <b>{_format_delay(value)}</b>",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("show-wdelay:"))
async def show_welcome_delay(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    bot = await _get_owned(owner, (callback.data or "").split(":")[1])
    if bot is None:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await send_delay_screen(message, bot, "welcome")
    await callback.answer()


@router.callback_query(F.data.regexp(r"^(wdelay|adelay):"))
async def change_delay(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    kind, raw_id, raw_delta = (callback.data or "").split(":")
    bot = await _get_owned(owner, raw_id)
    if bot is None:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    field = "welcome_delay_seconds" if kind == "wdelay" else "approval_delay_seconds"
    current = int(getattr(bot, field))
    value = 0 if raw_delta == "zero" else max(0, min(30 * 86400, current + int(raw_delta)))
    async with session_factory() as session:
        await update_delay(session, bot.id, field, value)
    setattr(bot, field, value)
    await message.delete()
    await send_delay_screen(message, bot, "welcome" if kind == "wdelay" else "approval")
    await callback.answer("Сохранено")


@router.callback_query(F.data.startswith("requests:"))
async def requests_screen(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    bot = await _get_owned(owner, (callback.data or "").split(":")[1])
    if bot is None:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    async with session_factory() as session:
        pending = await pending_requests(session, bot.id)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏱ Задержка принятия", callback_data=f"approval-delay:{bot.id}")],
            [
                InlineKeyboardButton(
                    text="⏸ Отключить" if bot.auto_approve else "✅ Принимать заявки",
                    callback_data=f"toggle-approval:{bot.id}",
                )
            ],
            [InlineKeyboardButton(text="⬅️ К боту", callback_data=f"bot:{bot.id}")],
        ]
    )
    await message.answer(
        f"📥 <b>Заявки · @{bot.username}</b>\n\n"
        f"Автопринятие: <b>{'включено' if bot.auto_approve else 'выключено'}</b>\n"
        f"Задержка: <b>{_format_delay(bot.approval_delay_seconds)}</b>\n"
        f"Ожидают в системе: <b>{pending}</b>",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("approval-delay:"))
async def approval_delay(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    bot = await _get_owned(owner, (callback.data or "").split(":")[1])
    if bot is None:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await send_delay_screen(message, bot, "approval")
    await callback.answer()


@router.callback_query(F.data.startswith("toggle-approval:"))
async def toggle_approval(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    bot = await _get_owned(owner, (callback.data or "").split(":")[1])
    if bot is None:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    async with session_factory() as session:
        enabled, changed = await toggle_auto_approve(session, bot)
    text = (
        f"Автопринятие включено. Накопленные заявки поставлены в очередь: {changed}."
        if enabled
        else f"Автопринятие отключено. {changed} отложенных заявок сохранено."
    )
    await callback.answer(text, show_alert=True)
    await message.answer(
        text, reply_markup=_one_button("📥 Открыть заявки", f"requests:{bot.id}")
    )


LANGUAGE_NAMES = {
    "ru": "🇷🇺 Русский",
    "en": "🇺🇸 Английский",
    "uk": "🇺🇦 Украинский",
    "ar": "🇸🇦 Арабский",
    "unknown": "🏳️ Не определён",
}


@router.callback_query(F.data.startswith("stats:"))
async def stats_screen(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    bot = await _get_owned(owner, (callback.data or "").split(":")[1])
    if bot is None:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    async with session_factory() as session:
        data = await bot_statistics(session, bot.id)
    languages = "\n".join(
        f"{LANGUAGE_NAMES.get(row['language_code'], '🌐 ' + row['language_code'])}: <b>{row['total']}</b>"
        for row in data["languages"]
    ) or "—"
    await message.answer(
        f"📊 <b>Статистика · @{bot.username}</b>\n\n"
        f"Всего: <b>{data['total']}</b>\n🟢 Живые: <b>{data['live']}</b>\n"
        f"🔴 Мёртвые: <b>{data['dead']}</b>\n⚪️ Не проверены: <b>{data['unknown']}</b>\n\n"
        f"<b>Пол</b>\n👨 Мужчины: <b>{data['male']}</b>\n👩 Женщины: <b>{data['female']}</b>\n"
        f"🤖 Трансформеры: <b>{data['transformer']}</b>\n\n<b>Языки</b>\n{languages}",
        reply_markup=_one_button("⬅️ К боту", f"bot:{bot.id}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete:"))
async def delete_prompt(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    bot = await _get_owned(owner, (callback.data or "").split(":")[1])
    if bot is None:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await message.answer(
        f"🗑 Удалить @{bot.username}?\n\nНастройки и статистика будут удалены без восстановления.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Да", callback_data=f"delete-confirm:{bot.id}"),
                    InlineKeyboardButton(text="❌ Нет", callback_data=f"bot:{bot.id}"),
                ]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete-confirm:"))
async def delete_confirm(callback: CallbackQuery, owner: Owner, state: FSMContext) -> None:
    message = _callback_message(callback)
    bot = await _get_owned(owner, (callback.data or "").split(":")[1])
    if bot is None:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    settings = get_settings()
    try:
        await remove_customer_webhook(bot, settings)
    except Exception:
        logger.exception("Could not remove customer webhook bot_id=%s", bot.id)
        await callback.answer(
            "Telegram не подтвердил отключение. Повторите позже — данные сохранены.",
            show_alert=True,
        )
        return
    async with session_factory() as session:
        keys = await bot_media_keys(session, bot.id)
    await ObjectStorage(settings).delete_many(keys)
    async with session_factory() as session:
        await delete_bot(session, bot.id)
    await state.clear()
    await message.answer(
        "✅ Бот удалён из Gramly Welcome. Сам Telegram-бот и права каналов не изменены."
    )
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(callback)
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_keyboard())
    await callback.answer()


@router.message(F.text.in_({"💎 ПОДПИСКА", "📈 Аналитика"}))
async def coming_soon(message: Message) -> None:
    await message.answer("В разработке...")


_bot: Bot | None = None
_dispatcher: Dispatcher | None = None


def interface_bot() -> Bot:
    global _bot
    if _bot is None:
        settings = get_settings()
        if not settings.interface_bot_token:
            raise RuntimeError("WELCOME_INTERFACE_BOT_TOKEN is not configured")
        _bot = Bot(
            settings.interface_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


def interface_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        settings = get_settings()
        storage = RedisStorage(
            redis.from_url(settings.valkey_url, decode_responses=False)  # type: ignore[no-untyped-call]
        )
        dispatcher = Dispatcher(storage=storage)
        middleware = OwnerMiddleware()
        dispatcher.message.outer_middleware(middleware)
        dispatcher.callback_query.outer_middleware(middleware)
        dispatcher.include_router(router)
        _dispatcher = dispatcher
    return _dispatcher


async def process_interface_update(payload: dict[str, Any]) -> None:
    update = Update.model_validate(payload)
    dispatcher = interface_dispatcher()
    storage = dispatcher.storage
    if not isinstance(storage, RedisStorage):
        raise RuntimeError("Owner bot requires Redis FSM storage")
    user_id = 0
    for key in ("message", "callback_query"):
        event = payload.get(key)
        sender = event.get("from") if isinstance(event, dict) else None
        if isinstance(sender, dict):
            user_id = int(sender.get("id") or 0)
            break
    async with storage.redis.lock(
        f"welcome:owner-lock:{user_id or update.update_id}",
        timeout=60,
        blocking_timeout=60,
    ):
        try:
            await dispatcher.feed_update(interface_bot(), update)
        except TelegramAPIError:
            # Business mutations are committed before response messages. A
            # transient Telegram response failure must not replay a completed
            # mutation such as a delay increment or approval toggle.
            logger.warning(
                "Owner bot response failed update_id=%s", update.update_id, exc_info=True
            )
