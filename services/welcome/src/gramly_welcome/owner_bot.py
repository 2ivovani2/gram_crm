from __future__ import annotations

import asyncio
import html
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
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    TelegramObject,
    Update,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .billing import create_crypto_checkout, create_stars_checkout, settle_stars_payment
from .bot_ui import (
    answer_with_ui_fallback,
    edit_with_ui_fallback,
    inline_button,
    load_bot_ui_theme,
)
from .commercial import (
    access_for_owner,
    ensure_free_access,
    feature_flag_enabled,
    payment_method_ready,
)
from .config import Settings, get_settings
from .content_service import (
    ContentValidationError,
    add_draft_step,
    copy_step,
    delete_step,
    draft_snapshot,
    ensure_default_farewell_flow,
    ensure_default_welcome_flow,
    move_step,
    open_draft,
    publish_draft,
    replace_step_keyboard,
    set_first_delay,
    set_flow_assignments,
    set_step_delay,
)
from .crypto import TokenKeyring
from .crypto_pay import CryptoPayClient, CryptoPayError
from .db import session_factory
from .finance import (
    FinanceError,
    available_balance,
    ensure_referral_code,
    record_first_touch,
)
from .flow_delivery import compile_preview_operations
from .learning import (
    HelpSnapshot,
    navigate_tip_session,
    open_help_session,
    schedule_learning_notifications,
)
from .models import ContentFlow, FlowChannelAssignment, ManagedBot, Owner, Payment, Plan
from .owner_repository import (
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
    set_webhook_configured,
    toggle_auto_approve,
    update_delay,
)
from .rotation import (
    owner_rotation_channels,
    rotation_statistics,
    set_priority_channel,
)
from .storage import MediaTooLargeError, ObjectStorage
from .telegram_delivery import send_compiled_operation

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


class WelcomeButtonState(StatesGroup):
    waiting_for_buttons = State()


class OwnerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            return None
        async with session_factory() as session:
            data["owner"] = await owner_from_telegram(session, user)
        result = await handler(event, data)
        if isinstance(event, (Message, CallbackQuery)):
            try:
                async with session_factory() as session:
                    await schedule_learning_notifications(session, data["owner"].id)
            except Exception:
                logger.exception(
                    "Learning notification scheduling failed owner_id=%s",
                    data["owner"].id,
                )
        return result


async def main_keyboard() -> InlineKeyboardMarkup:
    async with session_factory() as session:
        theme = await load_bot_ui_theme(session)
    settings = get_settings()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                inline_button(
                    "Открыть кабинет",
                    web_app=WebAppInfo(url=settings.mini_app_url),
                    style="primary",
                    emoji_key="important",
                    theme=theme,
                )
            ],
            [
                inline_button(
                    "Мои боты",
                    callback_data="menu:bots",
                    style="primary",
                    emoji_key="important",
                    theme=theme,
                )
            ],
            [
                inline_button("Подписка", callback_data="menu:subscription", theme=theme),
                inline_button("Аналитика", callback_data="menu:analytics", theme=theme),
            ],
            [inline_button("Партнёрская программа", callback_data="menu:referrals", theme=theme)],
            [
                inline_button(
                    "Помощь и советы",
                    callback_data="help:home",
                    style="success",
                    emoji_key="help",
                    theme=theme,
                )
            ],
        ]
    )


def _one_button(text: str, callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback)]])


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
            [InlineKeyboardButton(text="⚙️ Открыть карточку бота", callback_data=f"bot:{bot.id}")],
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


async def configure_customer_webhook(managed: ManagedBot, token: str, settings: Settings) -> None:
    url = f"{settings.public_webhook_base_url.rstrip('/')}/{managed.public_id}/{managed.path_secret}/"
    async with Bot(token=token) as bot:
        await bot.set_webhook(
            url,
            secret_token=managed.webhook_secret,
            allowed_updates=["message", "my_chat_member", "chat_member", "chat_join_request"],
            drop_pending_updates=False,
            max_connections=20,
        )


async def remove_customer_webhook(managed: ManagedBot, settings: Settings) -> None:
    token = TokenKeyring.parse(settings.token_encryption_keys).decrypt(managed.token_ciphertext)
    async with Bot(token=token) as bot:
        await bot.delete_webhook(drop_pending_updates=True)


@router.message(CommandStart())
async def start(message: Message, owner: Owner, state: FSMContext) -> None:
    await state.clear()
    command = (message.text or "").split(maxsplit=1)
    if len(command) == 2 and command[1].startswith("ref_"):
        async with session_factory() as session:
            await record_first_touch(session, owner.id, command[1][4:])
    await show_main_menu(message)


@router.callback_query(F.data.startswith("guide:"))
async def guide(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    step = int((callback.data or "").split(":")[1])
    if step >= len(GUIDE):
        async with session_factory() as session:
            await mark_guide_complete(session, owner.id, len(GUIDE))
        await message.answer("Готово — всё управление уже под рукой ✨", reply_markup=await main_keyboard())
        await show_bots(message, owner, 0)
        await callback.answer()
        return
    title, body = GUIDE[step]
    final = step == len(GUIDE) - 1
    markup = _one_button("🚀 Начать работу" if final else "➡️ Далее", f"guide:{step + 1}")
    content = f"{title}\n\n{body}\n\n<i>Шаг {step + 1} из {len(GUIDE)}</i>"
    if message.photo:
        # Keep pre-cutover onboarding messages usable after the owner webhook
        # moves away from Django.
        await message.edit_caption(caption=content, reply_markup=markup)
    else:
        await message.edit_text(content, reply_markup=markup)
    await callback.answer()


async def show_main_menu(message: Message) -> None:
    markup = await main_keyboard()
    await answer_with_ui_fallback(
        message,
        "🏠 <b>GramlyHello</b>\n\nУправление продуктом — в одном inline-меню:",
        reply_markup=markup,
    )


@router.callback_query(F.data == "menu:bots")
async def bots_menu_callback(callback: CallbackQuery, owner: Owner, state: FSMContext) -> None:
    await state.clear()
    await show_bots(_callback_message(callback), owner, 0)
    await callback.answer()


@router.message(F.text == "🤖 Мои Боты")
async def bots_menu(message: Message, owner: Owner, state: FSMContext) -> None:
    await state.clear()
    await show_bots(message, owner, 0)


async def show_bots(target: Message, owner: Owner, page: int) -> None:
    async with session_factory() as session:
        bots, total = await list_owned_bots(session, owner.id, page * BOTS_PER_PAGE, BOTS_PER_PAGE)
    page_count = max(1, (total + BOTS_PER_PAGE - 1) // BOTS_PER_PAGE)
    page = max(0, min(page, page_count - 1))
    keyboard = InlineKeyboardBuilder()
    for item in bots:
        keyboard.button(text=f"🤖 @{item.username or item.display_name}", callback_data=f"bot:{item.id}")
    keyboard.adjust(1)
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"bots:{page - 1}"))
    if page + 1 < page_count:
        navigation.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"bots:{page + 1}"))
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
                [InlineKeyboardButton(text="🤖 Открыть @BotFather", url="https://t.me/BotFather?start=bot")],
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
            await ensure_free_access(session, owner.id)
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
                    "Could not roll back customer webhook bot_id=%s",
                    managed.id,
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
                InlineKeyboardButton(text="👋 Прощание", callback_data=f"farewell:{bot.id}"),
                InlineKeyboardButton(text="🔄 Ротация", callback_data=f"rotation:{bot.id}"),
            ],
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data=f"stats:{bot.id}"),
                InlineKeyboardButton(text="🗑 Удалить бота", callback_data=f"delete:{bot.id}"),
            ],
            [InlineKeyboardButton(text="Помощь по управлению", callback_data="help:bots")],
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
    async with session_factory() as session:
        flow = await ensure_default_welcome_flow(session, bot.id, owner.id)
        draft = await open_draft(session, owner.id, flow.id, owner.telegram_id)
    await state.clear()
    await send_chain_editor(message, owner, draft.id)
    await callback.answer()


@router.callback_query(F.data.startswith("farewell:"))
async def set_farewell(callback: CallbackQuery, owner: Owner, state: FSMContext) -> None:
    message = _callback_message(callback)
    bot = await _get_owned(owner, (callback.data or "").split(":")[1])
    if bot is None:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    async with session_factory() as session:
        flow = await ensure_default_farewell_flow(session, bot.id, owner.id)
        draft = await open_draft(session, owner.id, flow.id, owner.telegram_id)
    await state.clear()
    await send_chain_editor(message, owner, draft.id)
    await callback.answer()


async def send_chain_editor(message: Message, owner: Owner, version_id: int) -> None:
    async with session_factory() as session:
        snapshot = await draft_snapshot(session, owner.id, version_id)
    is_farewell = snapshot.flow.kind == "farewell"
    lines = [
        "👋 <b>Редактор прощальной цепочки</b>"
        if is_farewell
        else "💬 <b>Редактор приветственной цепочки</b>",
        f"\nЧерновик версии <b>{snapshot.version.version}</b>",
        f"Первое сообщение: <b>{_format_delay(snapshot.version.first_delay_seconds)}</b>",
    ]
    if is_farewell:
        lines.append("Лимит: до 5 сообщений и до 5 файлов.")
    rows: list[list[InlineKeyboardButton]] = []
    if snapshot.steps:
        lines.append("")
        for index, step in enumerate(snapshot.steps, start=1):
            kind = str(step.payload.get("type") or "message")
            after = _format_delay(step.delay_after_seconds) if index < len(snapshot.steps) else "финал"
            lines.append(f"{index}. <b>{kind}</b> · после: {after}")
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{index} · {kind}",
                        callback_data=f"chain:step:{version_id}:{step.id}",
                    )
                ]
            )
    else:
        lines.append("\nПока нет шагов. Добавьте первое сообщение.")
    rows.extend(
        [
            [
                InlineKeyboardButton(text="➕ Добавить шаг", callback_data=f"chain:add:{version_id}"),
                InlineKeyboardButton(text="👁 Предпросмотр", callback_data=f"chain:preview:{version_id}"),
            ],
            [
                InlineKeyboardButton(text="− 5 сек до старта", callback_data=f"chain:first:{version_id}:-5"),
                InlineKeyboardButton(text="+ 5 сек до старта", callback_data=f"chain:first:{version_id}:5"),
            ],
            [
                InlineKeyboardButton(
                    text="📣 Назначить каналам",
                    callback_data=f"chain:assign:{version_id}",
                )
            ],
            [InlineKeyboardButton(text="🚀 Опубликовать", callback_data=f"chain:publish:{version_id}")],
            [InlineKeyboardButton(text="Помощь по цепочкам", callback_data="help:flows")],
            [InlineKeyboardButton(text="← К боту", callback_data=f"bot:{snapshot.flow.bot_id}")],
        ]
    )
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("chain:add:"))
async def add_chain_step(callback: CallbackQuery, owner: Owner, state: FSMContext) -> None:
    message = _callback_message(callback)
    version_id = int((callback.data or "").split(":")[2])
    try:
        async with session_factory() as session:
            snapshot = await draft_snapshot(session, owner.id, version_id)
    except ContentValidationError:
        await callback.answer("Черновик уже недоступен.", show_alert=True)
        return
    await state.set_state(WelcomeMessageState.waiting_for_message)
    await state.update_data(bot_id=snapshot.flow.bot_id, version_id=version_id)
    await message.answer(
        "➕ <b>Новый шаг</b>\n\nОтправьте текст или Telegram-вложение. "
        "Форматирование и Premium Emoji будут сохранены без преобразования.",
        reply_markup=_one_button(
            "❌ Отмена",
            f"farewell:{snapshot.flow.bot_id}"
            if snapshot.flow.kind == "farewell"
            else f"msg:{snapshot.flow.bot_id}",
        ),
    )
    await callback.answer()


@router.message(WelcomeMessageState.waiting_for_message)
async def receive_welcome(message: Message, owner: Owner, state: FSMContext) -> None:
    if message.content_type not in SUPPORTED_CONTENT:
        await message.answer("Этот тип сообщения нельзя безопасно воспроизвести.")
        return
    data = await state.get_data()
    bot = await _get_owned(owner, str(data.get("bot_id", "")))
    version_id = int(data.get("version_id") or 0)
    if bot is None or not version_id:
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
        attachments = [media] if media else []
        if message.media_group_id:
            if message.content_type not in {"photo", "video", "audio", "document"} or not media:
                raise ValueError("Этот тип вложения нельзя сохранить внутри Telegram-альбома.")
            current = await state.get_data()
            current_group = str(current.get("album_group") or "")
            if current_group and current_group != message.media_group_id:
                raise ValueError("Дождитесь сохранения предыдущего альбома.")
            album_items = list(current.get("album_items") or [])
            album_items.append({"payload": payload, "media": media})
            await state.update_data(
                album_group=message.media_group_id,
                album_items=album_items,
                album_last_message_id=message.message_id,
            )
            await asyncio.sleep(2)
            latest = await state.get_data()
            if int(latest.get("album_last_message_id") or 0) != message.message_id:
                return
            collected = list(latest.get("album_items") or [])
            payload = {
                "type": "media_group",
                "items": [item["payload"] for item in collected],
            }
            attachments = [item["media"] for item in collected]
        async with session_factory() as session:
            await add_draft_step(
                session,
                owner.id,
                version_id,
                payload,
                attachments,
                delay_after_seconds=1,
            )
    except (ValueError, MediaTooLargeError, ContentValidationError) as exc:
        await message.answer(f"⚠️ {exc}")
        return
    except Exception:
        if media:
            await storage.delete_many([str(media["storage_key"])])
        logger.exception("Could not save welcome message bot_id=%s", bot.id)
        await message.answer("Не удалось сохранить сообщение целиком. Попробуйте ещё раз.")
        return
    await state.clear()
    await message.answer("✅ Шаг добавлен в черновик. В production пока ничего не изменилось.")
    await send_chain_editor(message, owner, version_id)


@router.callback_query(F.data.startswith("chain:step:"))
async def chain_step(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    _, _, raw_version, raw_step = (callback.data or "").split(":")
    version_id, step_id = int(raw_version), int(raw_step)
    try:
        async with session_factory() as session:
            snapshot = await draft_snapshot(session, owner.id, version_id)
    except ContentValidationError:
        await callback.answer("Черновик уже недоступен.", show_alert=True)
        return
    step = next((item for item in snapshot.steps if item.id == step_id), None)
    if step is None:
        await callback.answer("Шаг не найден.", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="↑", callback_data=f"chain:move:{version_id}:{step_id}:-1"),
                InlineKeyboardButton(text="↓", callback_data=f"chain:move:{version_id}:{step_id}:1"),
                InlineKeyboardButton(text="⧉ Копия", callback_data=f"chain:copy:{version_id}:{step_id}"),
            ],
            [
                InlineKeyboardButton(text="− 5 сек", callback_data=f"chain:delay:{version_id}:{step_id}:-5"),
                InlineKeyboardButton(text="+ 5 сек", callback_data=f"chain:delay:{version_id}:{step_id}:5"),
                InlineKeyboardButton(text="+ 1 мин", callback_data=f"chain:delay:{version_id}:{step_id}:60"),
            ],
            [
                InlineKeyboardButton(
                    text="🔘 Клавиатура", callback_data=f"chain:buttons:{version_id}:{step_id}"
                )
            ],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"chain:delete:{version_id}:{step_id}")],
            [InlineKeyboardButton(text="← К цепочке", callback_data=f"chain:editor:{version_id}")],
        ]
    )
    await message.answer(
        f"<b>Шаг {step.position + 1}</b> · {step.payload.get('type', 'message')}\n"
        f"Задержка до следующего: <b>{_format_delay(step.delay_after_seconds)}</b>",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("chain:editor:"))
async def chain_editor_callback(callback: CallbackQuery, owner: Owner) -> None:
    await send_chain_editor(_callback_message(callback), owner, int((callback.data or "").split(":")[2]))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^chain:(move|copy|delete|delay|first):"))
async def mutate_chain(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    parts = (callback.data or "").split(":")
    action, version_id = parts[1], int(parts[2])
    orphaned_keys: list[str] = []
    try:
        async with session_factory() as session:
            if action == "move":
                await move_step(session, owner.id, int(parts[3]), int(parts[4]))
            elif action == "copy":
                await copy_step(session, owner.id, int(parts[3]))
            elif action == "delete":
                orphaned_keys = await delete_step(session, owner.id, int(parts[3]))
            elif action == "delay":
                snapshot = await draft_snapshot(session, owner.id, version_id)
                step_id, delta = int(parts[3]), int(parts[4])
                step = next((item for item in snapshot.steps if item.id == step_id), None)
                if step is None:
                    raise ContentValidationError("Editable step was not found")
                await set_step_delay(
                    session, owner.id, step_id, max(0, min(86_400, step.delay_after_seconds + delta))
                )
            elif action == "first":
                snapshot = await draft_snapshot(session, owner.id, version_id)
                await set_first_delay(
                    session,
                    owner.id,
                    version_id,
                    max(0, min(86_400, snapshot.version.first_delay_seconds + int(parts[3]))),
                )
    except ContentValidationError:
        await callback.answer("Не удалось изменить черновик.", show_alert=True)
        return
    if action == "delete" and orphaned_keys:
        try:
            await ObjectStorage(get_settings()).delete_many(orphaned_keys)
        except Exception:
            logger.exception("Could not delete orphaned draft media step_id=%s", parts[3])
    await send_chain_editor(message, owner, version_id)
    await callback.answer("Сохранено")


@router.callback_query(F.data.startswith("chain:publish:"))
async def publish_chain(callback: CallbackQuery, owner: Owner, state: FSMContext) -> None:
    message = _callback_message(callback)
    version_id = int((callback.data or "").split(":")[2])
    try:
        async with session_factory() as session:
            version = await publish_draft(session, owner.id, version_id)
            snapshot_flow_id = version.flow_id
            flow = await session.get(ContentFlow, snapshot_flow_id)
    except ContentValidationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await message.answer(
        f"🚀 <b>Цепочка опубликована</b> · версия {version.version}\n\n"
        f"Новые {'уходы' if flow and flow.kind == 'farewell' else 'вступления'} уже используют эту версию. "
        "Текущие доставки сохраняют свой snapshot.",
        reply_markup=_one_button("← К боту", f"bot:{flow.bot_id}" if flow else "bots:0"),
    )
    await callback.answer("Опубликовано")


async def send_rotation_screen(message: Message, owner: Owner, bot_id: int) -> None:
    async with session_factory() as session:
        access = await access_for_owner(session, owner.id)
        channels = await owner_rotation_channels(session, owner.id)
        statistics = await rotation_statistics(session, owner.id)
    if not access.entitlements.get("rotation", False):
        await message.answer(
            "🔄 <b>Ротация каналов</b>\n\nДоступна на тарифе Business. "
            "На Free приветствия работают с рекламой GramlyHello.",
            reply_markup=_one_button("← К боту", f"bot:{bot_id}"),
        )
        return
    rows = [
        [
            InlineKeyboardButton(
                text=("★ " if rotation.is_priority else "☆ ") + channel.title[:40],
                callback_data=f"rot-priority:{channel.id}:{bot_id}",
            )
        ]
        for rotation, channel in channels
    ]
    rows.append([InlineKeyboardButton(text="← К боту", callback_data=f"bot:{bot_id}")])
    rows.insert(-1, [InlineKeyboardButton(text="Помощь по ротации", callback_data="help:rotation")])
    await message.answer(
        "🔄 <b>Ротация каналов</b>\n\n"
        "Все подключённые каналы участвуют в общем Business-пуле. "
        "Звездой можно отметить до 7 своих приоритетных каналов.\n\n"
        f"Показы: <b>{statistics['impressions']}</b>\n"
        f"Подтверждённые подписки: <b>{statistics['conversions']}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("rotation:"))
async def rotation_screen(callback: CallbackQuery, owner: Owner) -> None:
    bot = await _get_owned(owner, (callback.data or "").split(":")[1])
    if bot is None:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await send_rotation_screen(_callback_message(callback), owner, bot.id)
    await callback.answer()


@router.callback_query(F.data.startswith("rot-priority:"))
async def toggle_rotation_priority(callback: CallbackQuery, owner: Owner) -> None:
    _, raw_channel_id, raw_bot_id = (callback.data or "").split(":")
    channel_id, bot_id = int(raw_channel_id), int(raw_bot_id)
    try:
        async with session_factory() as session:
            channels = await owner_rotation_channels(session, owner.id)
            target = next((item for item in channels if item[1].id == channel_id), None)
            if target is None:
                raise ValueError("Channel not found")
            await set_priority_channel(
                session,
                owner_id=owner.id,
                channel_id=channel_id,
                priority=not target[0].is_priority,
            )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await send_rotation_screen(_callback_message(callback), owner, bot_id)
    await callback.answer("Сохранено")


@router.callback_query(F.data.startswith("chain:preview:"))
async def preview_chain(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    version_id = int((callback.data or "").split(":")[2])
    try:
        async with session_factory() as session:
            snapshot = await draft_snapshot(session, owner.id, version_id)
            operations = await compile_preview_operations(session, snapshot.steps)
    except ContentValidationError:
        await callback.answer("Черновик уже недоступен.", show_alert=True)
        return
    await message.answer(
        "👁 <b>Предпросмотр цепочки</b>\n\n"
        "Ниже — реальные Telegram-вызовы будущей доставки. Задержки в preview пропущены."
    )
    storage = ObjectStorage(get_settings())
    try:
        for operation in operations:
            await send_compiled_operation(
                cast(Bot, message.bot),
                message.chat.id,
                operation.operation_type,
                operation.payload,
                operation.media,
                storage,
            )
    except (TelegramAPIError, OSError, ValueError):
        logger.exception("Could not preview draft version_id=%s", version_id)
        await message.answer(
            "⚠️ Telegram не смог воспроизвести один из элементов. "
            "Проверьте тип медиа, Premium Emoji и клавиатуру."
        )
    await callback.answer()


async def send_assignment_screen(message: Message, owner: Owner, version_id: int, page: int = 0) -> None:
    async with session_factory() as session:
        snapshot = await draft_snapshot(session, owner.id, version_id)
        channels = await bot_channels(session, snapshot.flow.bot_id)
        selected = set(
            await session.scalars(
                select(FlowChannelAssignment.channel_id).where(
                    FlowChannelAssignment.flow_id == snapshot.flow.id
                )
            )
        )
    page_size = 15
    pages = max(1, (len(channels) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    visible_channels = channels[page * page_size : (page + 1) * page_size]
    rows = [
        [
            InlineKeyboardButton(
                text=("✅ Все каналы" if snapshot.flow.assignment_mode == "all" else "Все каналы"),
                callback_data=f"chain:assign-all:{version_id}:{page}",
            )
        ]
    ]
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if channel.id in selected else ''}{channel.title}",
                    callback_data=f"chain:assign-toggle:{version_id}:{channel.id}:{page}",
                )
            ]
            for channel in visible_channels
        ]
    )
    if pages > 1:
        rows.append(
            [
                InlineKeyboardButton(text="←", callback_data=f"chain:assign:{version_id}:{max(0, page - 1)}"),
                InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"),
                InlineKeyboardButton(
                    text="→",
                    callback_data=f"chain:assign:{version_id}:{min(pages - 1, page + 1)}",
                ),
            ]
        )
    rows.append([InlineKeyboardButton(text="← К цепочке", callback_data=f"chain:editor:{version_id}")])
    await message.answer(
        "📣 <b>Назначение цепочки</b>\n\n"
        "Выберите все каналы либо конкретный набор. Новые подключённые каналы "
        "автоматически получают цепочку только в режиме «Все каналы».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("chain:assign:"))
async def chain_assign(callback: CallbackQuery, owner: Owner) -> None:
    parts = (callback.data or "").split(":")
    await send_assignment_screen(
        _callback_message(callback),
        owner,
        int(parts[2]),
        int(parts[3]) if len(parts) > 3 else 0,
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("chain:assign-all:"))
async def chain_assign_all(callback: CallbackQuery, owner: Owner) -> None:
    parts = (callback.data or "").split(":")
    version_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    try:
        async with session_factory() as session:
            snapshot = await draft_snapshot(session, owner.id, version_id)
            await set_flow_assignments(session, owner.id, snapshot.flow.id, None)
    except ContentValidationError:
        await callback.answer("Черновик уже недоступен.", show_alert=True)
        return
    await send_assignment_screen(_callback_message(callback), owner, version_id, page)
    await callback.answer("Назначено всем каналам")


@router.callback_query(F.data.startswith("chain:assign-toggle:"))
async def chain_assign_toggle(callback: CallbackQuery, owner: Owner) -> None:
    parts = (callback.data or "").split(":")
    _, _, raw_version, raw_channel = parts[:4]
    version_id, channel_id = int(raw_version), int(raw_channel)
    page = int(parts[4]) if len(parts) > 4 else 0
    try:
        async with session_factory() as session:
            snapshot = await draft_snapshot(session, owner.id, version_id)
            selected = set(
                await session.scalars(
                    select(FlowChannelAssignment.channel_id).where(
                        FlowChannelAssignment.flow_id == snapshot.flow.id
                    )
                )
            )
            if snapshot.flow.assignment_mode == "all":
                selected = {channel_id}
            elif channel_id in selected:
                selected.remove(channel_id)
            else:
                selected.add(channel_id)
            await set_flow_assignments(session, owner.id, snapshot.flow.id, sorted(selected))
    except ContentValidationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await send_assignment_screen(_callback_message(callback), owner, version_id, page)
    await callback.answer("Назначение обновлено")


@router.callback_query(F.data.startswith("chain:buttons:"))
async def configure_chain_buttons(callback: CallbackQuery, owner: Owner, state: FSMContext) -> None:
    message = _callback_message(callback)
    _, _, raw_version, raw_step = (callback.data or "").split(":")
    version_id, step_id = int(raw_version), int(raw_step)
    try:
        async with session_factory() as session:
            snapshot = await draft_snapshot(session, owner.id, version_id)
    except ContentValidationError:
        await callback.answer("Черновик уже недоступен.", show_alert=True)
        return
    if not any(item.id == step_id for item in snapshot.steps):
        await callback.answer("Шаг не найден.", show_alert=True)
        return
    await state.set_state(WelcomeButtonState.waiting_for_buttons)
    await state.update_data(version_id=version_id, step_id=step_id)
    await message.answer(
        "🔘 <b>Клавиатура шага</b>\n\n"
        "Отправьте кнопки строками:\n"
        "<code>url | Сайт | https://gramly.tech | primary</code>\n"
        "<code>callback | Дальше | next | success</code>\n\n"
        "Для reply-клавиатуры: <code>reply | Продолжить</code>. "
        "Допустимые стили: default, primary, success, danger.\n\n"
        "Отправьте <code>удалить</code>, чтобы убрать клавиатуру.",
        reply_markup=_one_button("❌ Отмена", f"chain:editor:{version_id}"),
    )
    await callback.answer()


def _parse_keyboard_definition(raw: str) -> dict[str, Any] | None:
    if raw.strip().lower() == "удалить":
        return None
    rows: list[list[dict[str, str]]] = []
    kind: str | None = None
    for raw_line in raw.splitlines():
        if not raw_line.strip():
            continue
        row = []
        for definition in raw_line.split(";;"):
            parts = [part.strip() for part in definition.split("|")]
            action = parts[0].lower() if parts else ""
            if action == "reply" and len(parts) == 2:
                button_kind, value, style = "reply", parts[1], "default"
                button = {"text": value, "action_type": "text", "value": value, "style": style}
            elif action in {"url", "callback"} and len(parts) in {3, 4}:
                button_kind = "inline"
                style = parts[3].lower() if len(parts) == 4 else "default"
                button = {"text": parts[1], "action_type": action, "value": parts[2], "style": style}
            else:
                raise ContentValidationError("Неверный формат кнопки")
            if kind is not None and kind != button_kind:
                raise ContentValidationError("Inline и reply-кнопки нельзя смешивать")
            kind = button_kind
            row.append(button)
        if row:
            rows.append(row)
    if not rows or kind is None:
        raise ContentValidationError("Добавьте хотя бы одну кнопку")
    return {"kind": kind, "settings": {"resize_keyboard": True}, "rows": rows}


@router.message(WelcomeButtonState.waiting_for_buttons)
async def receive_chain_buttons(message: Message, owner: Owner, state: FSMContext) -> None:
    data = await state.get_data()
    version_id, step_id = int(data.get("version_id") or 0), int(data.get("step_id") or 0)
    try:
        keyboard = _parse_keyboard_definition(message.text or "")
        async with session_factory() as session:
            await replace_step_keyboard(session, owner.id, step_id, keyboard)
    except ContentValidationError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await state.clear()
    await message.answer("✅ Клавиатура шага сохранена.")
    await send_chain_editor(message, owner, version_id)


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
            [InlineKeyboardButton(text="Помощь по заявкам", callback_data="help:auto_approve")],
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
    await message.answer(text, reply_markup=_one_button("📥 Открыть заявки", f"requests:{bot.id}"))


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
    languages = (
        "\n".join(
            f"{LANGUAGE_NAMES.get(row['language_code'], '🌐 ' + row['language_code'])}: <b>{row['total']}</b>"
            for row in data["languages"]
        )
        or "—"
    )
    await message.answer(
        f"📊 <b>Статистика · @{bot.username}</b>\n\n"
        f"Каналы: <b>{data['channels']}</b>\nЗаявки: <b>{data['join_requests']}</b>\n"
        f"Вступления / контакты: <b>{data['total']}</b>\n\n"
        f"Доставки: <b>{data['deliveries']}</b>\n✅ Успешно: <b>{data['delivered']}</b>\n"
        f"⚠️ Частично: <b>{data['partial']}</b>\n❌ Ошибки цепочек: <b>{data['failed']}</b>\n"
        f"🚫 Нельзя написать первым: <b>{data['unreachable']}</b>\n"
        f"Ошибки отдельных операций: <b>{data['operation_errors']}</b>\n\n"
        f"🟢 Доступные контакты: <b>{data['live']}</b>\n"
        f"🔴 Мёртвые: <b>{data['dead']}</b>\n⚪️ Не проверены: <b>{data['unknown']}</b>\n\n"
        f"<b>Языки Telegram</b>\n{languages}\n\n"
        "ℹ️ Telegram не передаёт пол и страну пользователя, поэтому эти разрезы не вычисляются.",
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
    await message.answer("✅ Бот удалён из Gramly Welcome. Сам Telegram-бот и права каналов не изменены.")
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(callback)
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=await main_keyboard())
    await callback.answer()


@router.message(F.text == "💎 ПОДПИСКА")
async def subscription_menu(message: Message, owner: Owner) -> None:
    async with session_factory() as session:
        access = await access_for_owner(session, owner.id)
        plan = await session.scalar(select(Plan).where(Plan.slug == "business"))
        stars_enabled = await feature_flag_enabled(session, "telegram_stars_checkout")
        crypto_enabled = await feature_flag_enabled(session, "crypto_pay_bot_checkout")
    keyboard = InlineKeyboardBuilder()
    if plan is not None and stars_enabled and payment_method_ready(plan, "telegram_stars"):
        keyboard.button(text=f"⭐ Оплатить {plan.price_xtr} Stars", callback_data="pay:stars")
    if plan is not None and crypto_enabled and payment_method_ready(plan, "crypto_pay"):
        keyboard.button(text=f"💎 Оплатить {plan.price_rub} ₽", callback_data="pay:crypto")
    keyboard.adjust(1)
    status_text = (
        f"Business активен до {access.ends_at:%d.%m.%Y}"
        if access.plan_slug == "business" and access.ends_at is not None
        else "Сейчас у вас Free: в приветствия добавляется реклама, ротация недоступна."
    )
    await message.answer(
        "💎 <b>Подписка GramlyHello</b>\n\n"
        f"{status_text}\n\nBusiness убирает рекламу и включает ротацию каналов.",
        reply_markup=keyboard.as_markup() if keyboard.buttons else None,
    )


@router.callback_query(F.data == "menu:subscription")
async def subscription_menu_callback(callback: CallbackQuery, owner: Owner) -> None:
    await subscription_menu(_callback_message(callback), owner)
    await callback.answer()


@router.callback_query(F.data == "pay:crypto")
async def pay_crypto(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    settings = get_settings()
    try:
        async with session_factory() as session:
            payment = await create_crypto_checkout(
                session,
                owner.id,
                CryptoPayClient(settings.crypto_pay_api_token, settings.crypto_pay_api_base_url),
                surface="bot",
            )
    except (FinanceError, CryptoPayError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await message.answer(
        "Счёт создан на 1 месяц Business. После оплаты подписка включится автоматически.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Оплатить в Crypto Bot", url=payment.invoice_url)]]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "pay:stars")
async def pay_stars(callback: CallbackQuery, owner: Owner) -> None:
    message = _callback_message(callback)
    try:
        async with session_factory() as session:
            payment = await create_stars_checkout(session, owner.id)
            plan = await session.get(Plan, payment.plan_id)
    except FinanceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if plan is None or plan.price_xtr is None:
        await callback.answer("Цена Stars не настроена.", show_alert=True)
        return
    await message.answer_invoice(
        title="GramlyHello Business",
        description="Business на 30 дней: без рекламы и с ротацией каналов.",
        payload=str(payment.checkout_token),
        currency="XTR",
        prices=[LabeledPrice(label="Business · 30 дней", amount=plan.price_xtr)],
        provider_token="",
        subscription_period=2_592_000,
    )
    await callback.answer()


@router.pre_checkout_query()
async def approve_stars_checkout(query: PreCheckoutQuery) -> None:
    try:
        async with session_factory() as session:
            payment = await session.scalar(
                select(Payment).where(
                    Payment.checkout_token == uuid.UUID(query.invoice_payload),
                    Payment.provider == "telegram_stars",
                    Payment.status == "created",
                )
            )
        valid = payment is not None and payment.original_amount == query.total_amount
    except (ValueError, FinanceError):
        valid = False
    await query.answer(ok=valid, error_message=None if valid else "Счёт больше не действителен")


@router.message(F.successful_payment)
async def stars_paid(message: Message, owner: Owner) -> None:
    payment = message.successful_payment
    if payment is None:
        return
    try:
        async with session_factory() as session:
            await settle_stars_payment(
                session,
                payload=payment.invoice_payload,
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
                stars=payment.total_amount,
            )
    except FinanceError:
        logger.exception("Stars payment reconciliation failed owner_id=%s", owner.id)
        await message.answer("Платёж получен, но требует сверки. Мы уже сохранили его идентификатор.")
        return
    await message.answer("✅ Business активирован на 30 дней.", reply_markup=await main_keyboard())


@router.message(F.text == "🤝 Партнёрская программа")
async def referral_dashboard(message: Message, owner: Owner) -> None:
    settings = get_settings()
    async with session_factory() as session:
        code = await ensure_referral_code(session, owner.id)
        balance = await available_balance(session, owner.id)
    await message.answer(
        "🤝 <b>Партнёрская программа</b>\n\n"
        "Ставка зависит от числа активных платных клиентов: 15% / 25% / 35%. "
        "Начисления идут 12 месяцев с первой оплаты реферала.\n\n"
        f"Баланс: <b>{balance} ₽</b>\n"
        f"Ваша ссылка:\nhttps://t.me/{settings.interface_bot_username}?start=ref_{code.code}\n\n"
        "Минимальная сумма вывода — 1 000 ₽. Управление заявками появится в Mini App.",
    )


@router.callback_query(F.data == "menu:referrals")
async def referral_dashboard_callback(callback: CallbackQuery, owner: Owner) -> None:
    await referral_dashboard(_callback_message(callback), owner)
    await callback.answer()


@router.message(F.text == "📈 Аналитика")
async def analytics_placeholder(message: Message) -> None:
    await message.answer("Подробная аналитика доступна в Mini App.")


@router.callback_query(F.data == "menu:analytics")
async def analytics_placeholder_callback(callback: CallbackQuery) -> None:
    await analytics_placeholder(_callback_message(callback))
    await callback.answer()


def _help_markup(snapshot: HelpSnapshot, theme: Any) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if snapshot.manual_url:
        rows.append(
            [
                inline_button(
                    "Открыть подробную инструкцию",
                    url=snapshot.manual_url,
                    style="primary",
                    emoji_key="guide",
                    theme=theme,
                )
            ]
        )
    if snapshot.session_id is not None and snapshot.tip_count > 1:
        token = snapshot.session_id.hex
        rows.append(
            [
                inline_button("Назад", callback_data=f"tip:{token}:-1", theme=theme),
                inline_button(
                    f"{snapshot.tip_index + 1}/{snapshot.tip_count}",
                    callback_data="noop",
                    theme=theme,
                ),
                inline_button("Дальше", callback_data=f"tip:{token}:1", theme=theme),
            ]
        )
    rows.append([inline_button("Главное меню", callback_data="menu:home", theme=theme)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _help_text(snapshot: HelpSnapshot) -> str:
    parts = [f"<b>{html.escape(snapshot.title)}</b>"]
    if snapshot.body:
        parts.append(html.escape(snapshot.body))
    if snapshot.tip is not None:
        parts.append(
            "<b>Практический совет</b>\n"
            + html.escape(snapshot.tip.text)
            + f"\n\n<i>{snapshot.tip_index + 1} из {snapshot.tip_count}</i>"
        )
    return "\n\n".join(parts)


@router.callback_query(F.data == "menu:home")
async def main_menu_callback(callback: CallbackQuery) -> None:
    await show_main_menu(_callback_message(callback))
    await callback.answer()


@router.callback_query(F.data.startswith("help:"))
async def contextual_help_screen(callback: CallbackQuery, owner: Owner) -> None:
    feature_key = (callback.data or "help:home").split(":", 1)[1][:64]
    try:
        async with session_factory() as session:
            snapshot = await open_help_session(session, owner.id, feature_key)
            theme = await load_bot_ui_theme(session)
    except Exception:
        logger.exception("Contextual help failed owner_id=%s feature=%s", owner.id, feature_key)
        await callback.answer("Подсказка временно недоступна. Настройки продолжат работать.", show_alert=True)
        return
    await answer_with_ui_fallback(
        _callback_message(callback),
        _help_text(snapshot),
        reply_markup=_help_markup(snapshot, theme),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tip:"))
async def tip_navigation(callback: CallbackQuery, owner: Owner) -> None:
    try:
        _, raw_session, raw_delta = (callback.data or "").split(":")
        async with session_factory() as session:
            snapshot = await navigate_tip_session(
                session,
                owner.id,
                uuid.UUID(hex=raw_session),
                int(raw_delta),
            )
            theme = await load_bot_ui_theme(session)
    except (ValueError, TypeError):
        snapshot = None
        theme = None
    except Exception:
        logger.exception("Tip navigation failed owner_id=%s", owner.id)
        snapshot = None
        theme = None
    if snapshot is None:
        await callback.answer("Сессия советов закончилась. Откройте справку ещё раз.", show_alert=True)
        return
    await edit_with_ui_fallback(
        _callback_message(callback),
        _help_text(snapshot),
        reply_markup=_help_markup(snapshot, theme),
    )
    await callback.answer()


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
        dispatcher.pre_checkout_query.outer_middleware(middleware)
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
    for key in ("message", "callback_query", "pre_checkout_query"):
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
            logger.warning("Owner bot response failed update_id=%s", update.update_id, exc_info=True)
