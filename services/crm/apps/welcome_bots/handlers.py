from __future__ import annotations

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files.storage import default_storage
from django.db import IntegrityError
from django.db.models import Count, Q

from .models import ManagedBot, Owner
from .services import (
    create_managed_bot,
    append_album_item,
    disable_auto_approve,
    enable_auto_approve,
    owned_bot,
    save_welcome_message,
    statistics,
)
from .states import AddBotState, WelcomeMessageState
from .telegram_api import (
    configure_customer_webhook,
    download_message_media,
    make_bot,
    remove_customer_webhook,
    serialize_message,
)

logger = logging.getLogger(__name__)
router = Router(name="gramly-welcome-interface")
BOTS_PER_PAGE = 6
SUPPORTED_CONTENT = {
    "text", "animation", "audio", "contact", "dice", "document", "location",
    "photo", "poll", "sticker", "venue", "video", "video_note", "voice",
}

GUIDE = [
    ("✨ <b>Всё управление — в одном месте</b>", "Подключайте свои боты, управляйте каналами, приветствиями и заявками прямо из Telegram."),
    ("🤖 <b>Подключение за три шага</b>", "Создайте бота в @BotFather → пришлите сюда его токен → нажмите кнопку «Добавить в канал». Канал определится автоматически."),
    ("💬 <b>Приветственные сообщения</b>", "Сохраняйте текст, форматирование и медиа. Для новых подписчиков можно настроить удобную задержку."),
    ("📥 <b>Заявки на вступление</b>", "Включите автоматическое принятие и задайте задержку. Каждая заявка обрабатывается независимо."),
    ("📊 <b>Актуальная статистика</b>", "Следите за доставкой, языками и общей аудиторией каждого подключённого бота."),
    ("🚀 <b>Можно начинать</b>", "Подключите первого бота — остальную рутину Gramly Welcome возьмёт на себя."),
]


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
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback)]])


def _add_to_channel_url(bot: ManagedBot) -> str:
    # Telegram opens the channel picker and promotes the bot with the minimum
    # right required to receive and approve join requests.
    return f"https://t.me/{bot.username}?startchannel&admin=invite_users+manage_chat"


def _connection_keyboard(bot: ManagedBot) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"➕ Добавить @{bot.username} в канал", url=_add_to_channel_url(bot))],
        [InlineKeyboardButton(text="🔄 Проверить подключение", callback_data=f"connect-check:{bot.id}")],
        [InlineKeyboardButton(text="⚙️ Открыть карточку бота", callback_data=f"bot:{bot.id}")],
    ])


def _hero() -> FSInputFile:
    return FSInputFile(Path(settings.BASE_DIR) / "static" / "welcome_bots" / "onboarding-hero.png")


@router.message(CommandStart())
async def start(message: Message, owner: Owner, state: FSMContext) -> None:
    await state.clear()
    if owner.guide_completed:
        await show_main_menu(message)
        return
    await message.answer_photo(
        _hero(),
        caption=(
            "👋 <b>Добро пожаловать в Gramly Welcome!</b>\n\n"
            "Подключайте собственных Telegram-ботов, автоматически принимайте заявки, "
            "отправляйте приветствия и следите за результатом — без отдельной панели.\n\n"
            "Короткое знакомство займёт меньше минуты."
        ),
        reply_markup=_one_button("➡️ Далее", "guide:0"),
    )


@router.callback_query(F.data.startswith("guide:"))
async def guide(callback: CallbackQuery, owner: Owner) -> None:
    step = int(callback.data.split(":")[1])
    if step >= len(GUIDE):
        await sync_to_async(Owner.objects.filter(pk=owner.pk).update)(guide_completed=True, guide_step=len(GUIDE))
        await callback.message.answer("Готово — всё управление уже под рукой ✨", reply_markup=main_keyboard())
        await show_bots(callback.message, owner, 0)
        await callback.answer()
        return
    title, body = GUIDE[step]
    final = step == len(GUIDE) - 1
    markup = _one_button("🚀 Начать работу" if final else "➡️ Далее", f"guide:{step + 1}")
    await callback.message.edit_caption(caption=f"{title}\n\n{body}\n\n<i>Шаг {step + 1} из {len(GUIDE)}</i>", reply_markup=markup)
    await callback.answer()


async def show_main_menu(message: Message) -> None:
    await message.answer("🏠 <b>Главное меню</b>\n\nВыберите раздел:", reply_markup=main_keyboard())


@router.message(F.text == "🤖 Мои Боты")
async def bots_menu(message: Message, owner: Owner, state: FSMContext) -> None:
    await state.clear()
    await show_bots(message, owner, 0)


async def show_bots(target: Message, owner: Owner, page: int) -> None:
    qs = ManagedBot.objects.filter(owner=owner, is_active=True)
    total = await sync_to_async(qs.count)()
    page_count = max(1, (total + BOTS_PER_PAGE - 1) // BOTS_PER_PAGE)
    page = max(0, min(page, page_count - 1))
    bots = await sync_to_async(list)(qs.order_by("created_at")[page * BOTS_PER_PAGE:(page + 1) * BOTS_PER_PAGE])
    kb = InlineKeyboardBuilder()
    for item in bots:
        kb.button(text=f"🤖 @{item.username or item.display_name}", callback_data=f"bot:{item.id}")
    kb.adjust(1)
    if page_count > 1:
        nav = []
        if page:
            nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"bots:{page - 1}"))
        if page + 1 < page_count:
            nav.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"bots:{page + 1}"))
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text="➕ Создать Бота", callback_data="bot:add"))
    text = "У Вас пока нет подключенных ботов." if not total else f"<b>Ваши боты</b> · {total}\n\nВыберите бота для управления:"
    await target.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("bots:"))
async def paginate_bots(callback: CallbackQuery, owner: Owner) -> None:
    await callback.message.delete()
    await show_bots(callback.message, owner, int(callback.data.split(":")[1]))
    await callback.answer()


@router.callback_query(F.data == "bot:add")
async def add_bot(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddBotState.waiting_for_token)
    await callback.message.answer(
        "🤖 <b>Подключение бота · шаг 1 из 3</b>\n\n"
        "<b>Если бот уже создан:</b> скопируйте его токен из сообщения @BotFather и отправьте сюда.\n\n"
        "<b>Если бота ещё нет:</b>\n"
        "1. Откройте @BotFather кнопкой ниже.\n"
        "2. Нажмите <b>Start</b> и отправьте команду <code>/newbot</code>.\n"
        "3. Задайте имя и username, заканчивающийся на <code>bot</code>.\n"
        "4. Скопируйте строку вида <code>123456789:AA...</code> и пришлите её сюда.\n\n"
        "🔒 Сообщение с токеном удалится сразу после получения.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🤖 Открыть @BotFather", url="https://t.me/BotFather?start=bot")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
        ]),
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
    probe = None
    managed = None
    try:
        probe = make_bot(token)
        info = await probe.get_me()
        managed = await sync_to_async(create_managed_bot)(owner, token, info)
        await configure_customer_webhook(managed)
        await sync_to_async(ManagedBot.objects.filter(pk=managed.pk).update)(webhook_configured=True)
    except IntegrityError:
        await message.answer("Этот бот уже подключён к системе другим владельцем.")
        return
    except TelegramAPIError:
        if managed:
            await sync_to_async(managed.delete)()
        await message.answer("Не удалось проверить токен или настроить webhook. Проверьте токен и попробуйте ещё раз.")
        return
    except Exception:
        logger.exception("Could not register customer bot")
        if managed:
            await sync_to_async(managed.delete)()
        await message.answer("Telegram временно недоступен. Попробуйте подключить бота чуть позже.")
        return
    finally:
        if probe:
            await probe.session.close()
    await state.clear()
    await message.answer(
        f"✅ <b>Токен принят · шаг 2 из 3</b>\n\n"
        f"Бот <b>@{managed.username}</b> зарегистрирован в Gramly.\n\n"
        "Теперь нажмите большую кнопку ниже, выберите свой канал и подтвердите добавление бота администратором. "
        "Право <b>«Добавление подписчиков»</b> нужно для автоматического принятия заявок.\n\n"
        "Ничего копировать из канала не требуется — Gramly обнаружит его автоматически.",
        reply_markup=_connection_keyboard(managed),
    )


@router.callback_query(F.data.startswith("connect:"))
async def connection_help(callback: CallbackQuery, owner: Owner) -> None:
    try:
        bot = await sync_to_async(owned_bot)(owner.id, int(callback.data.split(":")[1]))
    except ManagedBot.DoesNotExist:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.message.answer(
        f"🔗 <b>Подключение @{bot.username} к каналу</b>\n\n"
        "1. Нажмите «Добавить в канал».\n"
        "2. Выберите канал, которым Вы управляете.\n"
        "3. Не отключайте право «Добавление подписчиков».\n"
        "4. Подтвердите назначение администратором.\n\n"
        "Обычно Gramly обнаруживает канал за несколько секунд.",
        reply_markup=_connection_keyboard(bot),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("connect-check:"))
async def connection_check(callback: CallbackQuery, owner: Owner) -> None:
    try:
        bot = await sync_to_async(owned_bot)(owner.id, int(callback.data.split(":")[1]))
    except ManagedBot.DoesNotExist:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    channels = await sync_to_async(list)(bot.channels.filter(is_active=True).values_list("title", flat=True))
    if not channels:
        await callback.answer(
            "Пока не вижу канал. Убедитесь, что бот добавлен именно администратором, и нажмите ещё раз через несколько секунд.",
            show_alert=True,
        )
        return
    names = "\n".join(f"• {title}" for title in channels)
    await callback.message.answer(
        f"🎉 <b>Подключение завершено · шаг 3 из 3</b>\n\n"
        f"@{bot.username} обслуживает:\n{names}\n\n"
        "Теперь откройте карточку и задайте приветствие в разделе «Сообщения».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Настроить приветствие", callback_data=f"msg:{bot.id}")],
            [InlineKeyboardButton(text="⚙️ Открыть карточку", callback_data=f"bot:{bot.id}")],
        ]),
    )
    await callback.answer("Канал подключён")


@router.callback_query(F.data.startswith("bot:"))
async def bot_card(callback: CallbackQuery, owner: Owner) -> None:
    tail = callback.data.split(":", 1)[1]
    if tail == "add":
        return
    try:
        bot = await sync_to_async(owned_bot)(owner.id, int(tail))
    except ManagedBot.DoesNotExist:
        await callback.answer("Бот не найден или Вам не принадлежит.", show_alert=True)
        return
    await send_bot_card(callback.message, bot)
    await callback.answer()


async def send_bot_card(message: Message, bot: ManagedBot) -> None:
    counts = await sync_to_async(lambda: bot.contacts.aggregate(
        live=Count("id", filter=Q(delivery_status="live")),
        dead=Count("id", filter=Q(delivery_status="dead")),
    ))()
    channels = await sync_to_async(list)(
        bot.channels.filter(is_active=True).values_list("title", flat=True)[:10]
    )
    channel_text = "\n".join(f"  • {title}" for title in channels) if channels else "  ⚠️ Пока не подключено ни одного канала"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Подключить канал", callback_data=f"connect:{bot.id}")],
        [InlineKeyboardButton(text="💬 Сообщения", callback_data=f"msg:{bot.id}"), InlineKeyboardButton(text="📥 Заявки", callback_data=f"requests:{bot.id}")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data=f"stats:{bot.id}"), InlineKeyboardButton(text="🗑 Удалить бота", callback_data=f"delete:{bot.id}")],
    ])
    await message.answer(
        f"🤖 <b>@{bot.username or bot.display_name}</b>\n"
        f"<code>{bot.telegram_id}</code>\n\n"
        f"🟢 Живые: <b>{counts['live']}</b>\n🔴 Мёртвые: <b>{counts['dead']}</b>\n"
        f"📣 Каналы: <b>{len(channels)}</b>\n{channel_text}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("msg:"))
async def set_message(callback: CallbackQuery, owner: Owner, state: FSMContext) -> None:
    bot_id = int(callback.data.split(":")[1])
    try:
        await sync_to_async(owned_bot)(owner.id, bot_id)
    except ManagedBot.DoesNotExist:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.set_state(WelcomeMessageState.waiting_for_message)
    await state.update_data(bot_id=bot_id)
    await callback.message.answer(
        "💬 <b>Новое приветствие</b>\n\nОтправьте одно сообщение: текст или поддерживаемое Telegram-вложение. "
        "Форматирование, подпись и эмодзи сохранятся.",
        reply_markup=_one_button("❌ Отмена", f"bot:{bot_id}"),
    )
    await callback.answer()


@router.message(WelcomeMessageState.waiting_for_message)
async def receive_welcome(message: Message, owner: Owner, state: FSMContext) -> None:
    if message.content_type not in SUPPORTED_CONTENT:
        await message.answer("Этот тип сообщения Telegram нельзя безопасно воспроизвести. Отправьте текст или обычное медиа.")
        return
    data = await state.get_data()
    try:
        bot = await sync_to_async(owned_bot)(owner.id, int(data["bot_id"]))
    except (ManagedBot.DoesNotExist, KeyError):
        await state.clear()
        await message.answer("Сессия настройки устарела. Откройте бота ещё раз.")
        return
    media_data = None
    try:
        media_data = await download_message_media(
            message.bot,
            message,
            f"welcome-bots/{bot.public_id}/messages",
        )
        payload = serialize_message(message)
        if message.media_group_id:
            if message.content_type not in {"photo", "video", "audio", "document"} or not media_data:
                raise ValueError("Этот тип вложения нельзя сохранить внутри Telegram-альбома.")
            draft = await sync_to_async(append_album_item)(
                bot,
                owner,
                message.media_group_id,
                message.message_id,
                payload,
                media_data,
            )
            from .tasks import finalize_welcome_album_task
            await sync_to_async(finalize_welcome_album_task.apply_async)(args=(draft.id,), countdown=2)
            await message.answer("📎 Часть альбома принята, сохраняю композицию…")
            return
        version = await sync_to_async(save_welcome_message)(bot, owner.telegram_id, payload, media_data)
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    except Exception:
        if media_data:
            await sync_to_async(default_storage.delete)(media_data["storage_key"])
        logger.exception("Could not save welcome message for bot %s", bot.id)
        await message.answer("Не удалось сохранить сообщение целиком. Попробуйте ещё раз.")
        return
    await state.clear()
    await message.answer(f"✅ Приветствие сохранено · версия {version.version}")
    await send_delay_screen(message, bot, "welcome")


@router.callback_query(F.data.startswith("show-wdelay:"))
async def show_welcome_delay(callback: CallbackQuery, owner: Owner) -> None:
    try:
        bot = await sync_to_async(owned_bot)(owner.id, int(callback.data.split(":")[1]))
    except ManagedBot.DoesNotExist:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await send_delay_screen(callback.message, bot, "welcome")
    await callback.answer()


def _format_delay(seconds: int) -> str:
    if not seconds:
        return "сразу"
    parts = []
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days: parts.append(f"{days} д")
    if hours: parts.append(f"{hours} ч")
    if minutes: parts.append(f"{minutes} мин")
    if seconds: parts.append(f"{seconds} сек")
    return " ".join(parts)


async def send_delay_screen(message: Message, bot: ManagedBot, kind: str) -> None:
    value = bot.welcome_delay_seconds if kind == "welcome" else bot.approval_delay_seconds
    title = "Задержка приветственного сообщения" if kind == "welcome" else "Задержка принятия"
    prefix = "wdelay" if kind == "welcome" else "adelay"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="− 5 мин", callback_data=f"{prefix}:{bot.id}:-300"), InlineKeyboardButton(text="+ 5 мин", callback_data=f"{prefix}:{bot.id}:300")],
        [InlineKeyboardButton(text="− 1 час", callback_data=f"{prefix}:{bot.id}:-3600"), InlineKeyboardButton(text="+ 1 час", callback_data=f"{prefix}:{bot.id}:3600")],
        [InlineKeyboardButton(text="Обнулить", callback_data=f"{prefix}:{bot.id}:zero")],
        [InlineKeyboardButton(text="✅ Готово", callback_data=f"bot:{bot.id}")],
    ])
    await message.answer(f"⏱ <b>{title}</b>\n\nТекущее значение: <b>{_format_delay(value)}</b>", reply_markup=kb)


@router.callback_query(F.data.regexp(r"^(wdelay|adelay):"))
async def change_delay(callback: CallbackQuery, owner: Owner) -> None:
    kind, raw_id, raw_delta = callback.data.split(":")
    try:
        bot = await sync_to_async(owned_bot)(owner.id, int(raw_id))
    except ManagedBot.DoesNotExist:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    field = "welcome_delay_seconds" if kind == "wdelay" else "approval_delay_seconds"
    current = getattr(bot, field)
    value = 0 if raw_delta == "zero" else max(0, min(30 * 86400, current + int(raw_delta)))
    setattr(bot, field, value)
    await sync_to_async(bot.save)(update_fields=(field, "updated_at"))
    await callback.message.delete()
    await send_delay_screen(callback.message, bot, "welcome" if kind == "wdelay" else "approval")
    await callback.answer("Сохранено")


@router.callback_query(F.data.startswith("requests:"))
async def requests_screen(callback: CallbackQuery, owner: Owner) -> None:
    bot_id = int(callback.data.split(":")[1])
    try:
        bot = await sync_to_async(owned_bot)(owner.id, bot_id)
    except ManagedBot.DoesNotExist:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    pending = await sync_to_async(bot.join_requests.filter(status="pending").count)()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ Задержка принятия", callback_data=f"approval-delay:{bot.id}")],
        [InlineKeyboardButton(text="⏸ Отключить" if bot.auto_approve else "✅ Принимать заявки", callback_data=f"toggle-approval:{bot.id}")],
        [InlineKeyboardButton(text="⬅️ К боту", callback_data=f"bot:{bot.id}")],
    ])
    await callback.message.answer(
        f"📥 <b>Заявки · @{bot.username}</b>\n\n"
        f"Автопринятие: <b>{'включено' if bot.auto_approve else 'выключено'}</b>\n"
        f"Задержка: <b>{_format_delay(bot.approval_delay_seconds)}</b>\n"
        f"Ожидают в системе: <b>{pending}</b>",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("approval-delay:"))
async def approval_delay(callback: CallbackQuery, owner: Owner) -> None:
    try:
        bot = await sync_to_async(owned_bot)(owner.id, int(callback.data.split(":")[1]))
    except ManagedBot.DoesNotExist:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await send_delay_screen(callback.message, bot, "approval")
    await callback.answer()


@router.callback_query(F.data.startswith("toggle-approval:"))
async def toggle_approval(callback: CallbackQuery, owner: Owner) -> None:
    try:
        bot = await sync_to_async(owned_bot)(owner.id, int(callback.data.split(":")[1]))
    except ManagedBot.DoesNotExist:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    if bot.auto_approve:
        restored = await sync_to_async(disable_auto_approve)(bot)
        text = f"Автопринятие отключено. {restored} отложенных заявок сохранено."
    else:
        queued = await sync_to_async(enable_auto_approve)(bot)
        text = f"Автопринятие включено. Накопленные заявки поставлены в очередь: {queued}."
    await callback.answer(text, show_alert=True)
    await callback.message.answer(text, reply_markup=_one_button("📥 Открыть заявки", f"requests:{bot.id}"))


LANGUAGE_NAMES = {"ru": "🇷🇺 Русский", "en": "🇺🇸 Английский", "uk": "🇺🇦 Украинский", "ar": "🇸🇦 Арабский", "unknown": "🏳️ Не определён"}


@router.callback_query(F.data.startswith("stats:"))
async def stats_screen(callback: CallbackQuery, owner: Owner) -> None:
    try:
        bot = await sync_to_async(owned_bot)(owner.id, int(callback.data.split(":")[1]))
    except ManagedBot.DoesNotExist:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    data = await sync_to_async(statistics)(bot)
    languages = "\n".join(f"{LANGUAGE_NAMES.get(row['language_code'], '🌐 ' + row['language_code'])}: <b>{row['total']}</b>" for row in data["languages"]) or "—"
    await callback.message.answer(
        f"📊 <b>Статистика · @{bot.username}</b>\n\n"
        f"Всего: <b>{data['total']}</b>\n🟢 Живые: <b>{data['live']}</b>\n"
        f"🔴 Мёртвые: <b>{data['dead']}</b>\n⚪️ Ещё не проверены: <b>{data['unknown']}</b>\n\n"
        f"<b>Пол</b>\n👨 Мужчины: <b>{data['male']}</b>\n👩 Женщины: <b>{data['female']}</b>\n"
        f"🤖 Трансформеры: <b>{data['transformer']}</b>\n\n<b>Языки</b>\n{languages}\n\n"
        "<i>Данные рассчитаны в момент запроса.</i>",
        reply_markup=_one_button("⬅️ К боту", f"bot:{bot.id}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete:"))
async def delete_prompt(callback: CallbackQuery, owner: Owner) -> None:
    try:
        bot = await sync_to_async(owned_bot)(owner.id, int(callback.data.split(":")[1]))
    except ManagedBot.DoesNotExist:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.message.answer(
        f"🗑 Удалить @{bot.username}?\n\nНастройки и статистика будут удалены без возможности восстановления.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Да", callback_data=f"delete-confirm:{bot.id}"), InlineKeyboardButton(text="❌ Нет", callback_data=f"bot:{bot.id}")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete-confirm:"))
async def delete_confirm(callback: CallbackQuery, owner: Owner, state: FSMContext) -> None:
    try:
        bot = await sync_to_async(owned_bot)(owner.id, int(callback.data.split(":")[1]))
    except ManagedBot.DoesNotExist:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    try:
        await remove_customer_webhook(bot)
    except Exception:
        logger.exception("Could not remove customer webhook before deletion")
        await callback.answer("Telegram не подтвердил отключение. Повторите позже — данные сохранены.", show_alert=True)
        return
    media_keys = await sync_to_async(_media_keys_for_bot)(bot.id)
    await sync_to_async(bot.delete)()
    for key in filter(None, media_keys):
        await sync_to_async(default_storage.delete)(key)
    await state.clear()
    await callback.message.answer("✅ Бот удалён из Gramly Welcome. Сам Telegram-бот и его права в каналах не изменены.")
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("Действие отменено.", reply_markup=main_keyboard())
    await callback.answer()


@router.message(F.text.in_({"💎 ПОДПИСКА", "📈 Аналитика"}))
async def coming_soon(message: Message) -> None:
    await message.answer("В разработке...")


def _media_keys_for_bot(bot_id: int) -> list[str]:
    from .models import WelcomeMedia

    return list(WelcomeMedia.objects.filter(version__message__bot_id=bot_id).values_list("storage_key", flat=True))
