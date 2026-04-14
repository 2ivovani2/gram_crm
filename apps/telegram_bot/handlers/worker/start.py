"""
Worker: /start command and join request flow.

New user flow:
  /start → not activated → show "Подать заявку" button
  → tap → JoinRequest created → all admins notified
  → admin approves → user activated + notified
  → admin rejects  → user notified, can re-apply

If user already has a pending request: show status screen.
"""
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from asgiref.sync import sync_to_async

from apps.telegram_bot.callbacks import WorkerCallback
from apps.telegram_bot.keyboards import get_main_menu_keyboard
from apps.users.models import User

router = Router(name="worker_start")


def _pending_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Обновить статус", callback_data=WorkerCallback(action="check_request").pack())
    return b.as_markup()


def _apply_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📝 Подать заявку", callback_data=WorkerCallback(action="submit_request").pack())
    return b.as_markup()


def _rejected_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📝 Подать повторно", callback_data=WorkerCallback(action="submit_request").pack())
    return b.as_markup()


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, db_user: User, state: FSMContext) -> None:
    await state.clear()

    # Handle referral deep-link: /start ref_<token>
    payload = command.args or ""
    if payload.startswith("ref_") and not db_user.referred_by:
        token = payload[4:]
        from apps.referrals.services import ReferralService
        from apps.users.services import UserService as _US
        referrer = await sync_to_async(ReferralService.resolve_token)(token)
        if referrer and referrer.pk != db_user.pk:
            await sync_to_async(_US.set_referred_by)(db_user, referrer)

    if db_user.is_admin():
        from apps.telegram_bot.handlers.admin.menu import send_admin_main_menu
        await send_admin_main_menu(message, db_user)
        return

    if db_user.is_curator():
        from apps.telegram_bot.handlers.curator.menu import send_curator_main_menu
        await send_curator_main_menu(message, db_user)
        return

    if db_user.is_activated:
        from django.conf import settings
        channels_url = getattr(settings, "CHANNELS_DB_URL", "")
        await message.answer(
            f"👋 С возвращением, <b>{db_user.display_name}</b>!\n\nВыберите действие:",
            reply_markup=get_main_menu_keyboard(channels_db_url=channels_url),
        )
        return

    await _show_not_activated(message, db_user)


async def _show_not_activated(event: Message | CallbackQuery, db_user: User) -> None:
    """Show appropriate screen based on join request status."""
    from apps.clients.services import JoinService
    request = await sync_to_async(JoinService.get_any_request)(db_user)

    if request is None:
        text = (
            f"👋 Привет, <b>{db_user.display_name}</b>!\n\n"
            "Для получения доступа необходимо подать заявку.\n"
            "Администратор рассмотрит её и уведомит вас о решении."
        )
        markup = _apply_keyboard()
    elif request.is_pending:
        text = (
            "⏳ <b>Заявка на рассмотрении</b>\n\n"
            "Ожидайте решения администратора — вы получите уведомление.\n\n"
            f"Заявка подана: {request.created_at.strftime('%d.%m.%Y %H:%M')} МСК"
        )
        markup = _pending_keyboard()
    elif request.status == "approved":
        # Approved but not activated yet — shouldn't normally happen
        text = "✅ Ваша заявка принята!\n\nНажмите /start чтобы продолжить."
        markup = None
    else:  # rejected
        text = (
            "❌ <b>Заявка отклонена</b>\n\n"
            "Вы можете подать заявку повторно."
        )
        markup = _rejected_keyboard()

    if isinstance(event, Message):
        await event.answer(text, reply_markup=markup)
    else:
        await event.message.edit_text(text, reply_markup=markup)


# ── Join request flow ─────────────────────────────────────────────────────────

@router.callback_query(WorkerCallback.filter(F.action == "submit_request"))
async def cb_submit_request(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await callback.answer()
    if db_user.is_activated:
        await callback.answer("Вы уже активированы!", show_alert=True)
        return

    from apps.clients.services import JoinService, JoinServiceError
    try:
        request = await sync_to_async(JoinService.submit)(db_user)
    except JoinServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.edit_text(
        "✅ <b>Заявка отправлена!</b>\n\n"
        "Администратор рассмотрит её в ближайшее время.\n"
        "Вы получите уведомление о решении.",
        reply_markup=_pending_keyboard(),
    )

    await _notify_admins_new_request(request, db_user, callback.bot)


@router.callback_query(WorkerCallback.filter(F.action == "check_request"))
async def cb_check_request(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await callback.answer()
    # Re-fetch to get current activation state
    from apps.users.services import UserService
    db_user = await sync_to_async(UserService.get_by_telegram_id)(db_user.telegram_id) or db_user

    if db_user.is_activated:
        from django.conf import settings
        channels_url = getattr(settings, "CHANNELS_DB_URL", "")
        await callback.message.edit_text(
            f"✅ Добро пожаловать, <b>{db_user.display_name}</b>!\n"
            "Ваша заявка принята. Выберите действие:",
            reply_markup=get_main_menu_keyboard(channels_db_url=channels_url),
        )
        return

    await _show_not_activated(callback, db_user)


# ── Navigation ────────────────────────────────────────────────────────────────

@router.callback_query(WorkerCallback.filter(F.action == "back_to_start"))
async def cb_back_to_start(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()

    from apps.users.services import UserService
    db_user = await sync_to_async(UserService.get_by_telegram_id)(db_user.telegram_id) or db_user

    if db_user.is_admin():
        from apps.telegram_bot.handlers.admin.menu import send_admin_main_menu
        await send_admin_main_menu(callback, db_user)
        return

    if db_user.is_curator():
        from apps.telegram_bot.handlers.curator.menu import send_curator_main_menu
        await send_curator_main_menu(callback, db_user)
        return

    if db_user.is_activated:
        from django.conf import settings
        channels_url = getattr(settings, "CHANNELS_DB_URL", "")
        await callback.message.edit_text(
            f"👋 Главное меню, <b>{db_user.display_name}</b>!",
            reply_markup=get_main_menu_keyboard(channels_db_url=channels_url),
        )
    else:
        await _show_not_activated(callback, db_user)


@router.callback_query(WorkerCallback.filter(F.action == "cancel"))
async def cb_cancel(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")

    from apps.users.services import UserService
    db_user = await sync_to_async(UserService.get_by_telegram_id)(db_user.telegram_id) or db_user

    if db_user.is_activated:
        from django.conf import settings
        channels_url = getattr(settings, "CHANNELS_DB_URL", "")
        await callback.message.edit_text(
            "Действие отменено.",
            reply_markup=get_main_menu_keyboard(channels_db_url=channels_url),
        )
    else:
        await _show_not_activated(callback, db_user)


# ── Admin notification helper ─────────────────────────────────────────────────

async def _notify_admins_new_request(request, user: User, bot) -> None:
    from apps.users.models import User as UserModel
    from apps.telegram_bot.callbacks import AdminApplicationCallback

    text = (
        "📋 <b>Новая заявка на вступление</b>\n\n"
        f"👤 Имя: <b>{user.display_name}</b>\n"
        f"🆔 Telegram ID: <code>{user.telegram_id}</code>"
    )
    if user.telegram_username:
        text += f"\n📱 @{user.telegram_username}"
    if user.referred_by:
        text += f"\n🤝 Реферал от: <b>{user.referred_by.display_name}</b>"

    kb = InlineKeyboardBuilder()
    kb.button(
        text="✅ Принять",
        callback_data=AdminApplicationCallback(action="approve", request_id=request.pk).pack(),
    )
    kb.button(
        text="❌ Отклонить",
        callback_data=AdminApplicationCallback(action="reject_ask", request_id=request.pk).pack(),
    )
    kb.adjust(2)
    markup = kb.as_markup()

    admin_ids = await sync_to_async(
        lambda: list(UserModel.objects.filter(role="admin", is_blocked_bot=False).values_list("telegram_id", flat=True))
    )()

    notifications = []
    for tg_id in admin_ids:
        try:
            msg = await bot.send_message(tg_id, text, reply_markup=markup)
            notifications.append({"telegram_id": tg_id, "message_id": msg.message_id})
        except Exception:
            pass

    # Save message IDs so we can edit them after decision
    if notifications:
        await sync_to_async(
            lambda: type(request).objects.filter(pk=request.pk).update(admin_notifications=notifications)
        )()
