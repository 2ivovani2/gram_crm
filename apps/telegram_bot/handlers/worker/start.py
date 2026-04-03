"""
Worker: /start command, invite key flow, main menu navigation.

Flow for new user:
  /start → show "enter invite key" menu
  → user taps button → FSM state: waiting_for_key
  → user sends key text → validate → activate OR show error

Flow for activated user:
  /start → show main worker menu
"""
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from asgiref.sync import sync_to_async

from apps.telegram_bot.callbacks import WorkerCallback
from apps.telegram_bot.keyboards import get_main_menu_keyboard, get_cancel_keyboard
from apps.telegram_bot.states import InviteKeyInputState
from apps.users.models import User

router = Router(name="worker_start")


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

    # Admins get redirected to their panel
    if db_user.is_admin():
        from apps.telegram_bot.handlers.admin.menu import send_admin_main_menu
        await send_admin_main_menu(message, db_user)
        return

    if db_user.is_activated:
        await message.answer(
            f"👋 С возвращением, <b>{db_user.display_name}</b>!\n\n"
            "Выберите действие:",
            reply_markup=get_main_menu_keyboard(is_activated=True),
        )
    else:
        await message.answer(
            f"👋 Привет, <b>{db_user.display_name}</b>!\n\n"
            "Для получения доступа вам нужен <b>invite key</b>.\n"
            "Нажмите кнопку ниже и введите ваш ключ.",
            reply_markup=get_main_menu_keyboard(is_activated=False),
        )


# ── Navigation ────────────────────────────────────────────────────────────────

@router.callback_query(WorkerCallback.filter(F.action == "back_to_start"))
async def cb_back_to_start(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()

    # Re-fetch to get current activation state
    from apps.users.services import UserService
    db_user = await sync_to_async(UserService.get_by_telegram_id)(db_user.telegram_id) or db_user

    if db_user.is_admin():
        from apps.telegram_bot.handlers.admin.menu import send_admin_main_menu
        await send_admin_main_menu(callback, db_user)
        return

    text = (
        f"👋 Главное меню, <b>{db_user.display_name}</b>!"
        if db_user.is_activated
        else "👋 Для доступа нужен <b>invite key</b>."
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_main_menu_keyboard(is_activated=db_user.is_activated),
    )


@router.callback_query(WorkerCallback.filter(F.action == "cancel"))
async def cb_cancel(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.edit_text(
        "Действие отменено.",
        reply_markup=get_main_menu_keyboard(is_activated=db_user.is_activated),
    )


# ── Invite key input flow ─────────────────────────────────────────────────────

@router.callback_query(WorkerCallback.filter(F.action == "enter_invite"))
async def cb_enter_invite(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if db_user.is_activated:
        await callback.answer("Вы уже активированы!", show_alert=True)
        return
    await callback.answer()
    await state.set_state(InviteKeyInputState.waiting_for_key)
    await callback.message.edit_text(
        "🔑 Введите ваш <b>invite key</b>:\n\n"
        "<i>Ключ не чувствителен к регистру. "
        "Обратитесь к администратору, если у вас нет ключа.</i>",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(InviteKeyInputState.waiting_for_key)
async def process_invite_key(message: Message, db_user: User, state: FSMContext) -> None:
    from apps.invites.services import InviteService, InviteValidationError

    raw_key = (message.text or "").strip()
    if not raw_key:
        await message.answer(
            "⚠️ Пожалуйста, отправьте текстовый ключ.",
            reply_markup=get_cancel_keyboard(),
        )
        return

    try:
        await sync_to_async(InviteService.validate_and_activate)(db_user, raw_key)
        await state.clear()

        # Refresh user from DB to get updated is_activated
        from apps.users.services import UserService
        db_user = await sync_to_async(UserService.get_by_telegram_id)(db_user.telegram_id)

        await message.answer(
            f"✅ <b>Активация прошла успешно!</b>\n\n"
            f"Добро пожаловать, <b>{db_user.display_name}</b>! "
            "Теперь у вас есть полный доступ.",
            reply_markup=get_main_menu_keyboard(is_activated=True),
        )
    except InviteValidationError as exc:
        await message.answer(
            f"❌ <b>Ошибка:</b> {exc}\n\n"
            "Попробуйте снова или обратитесь к администратору.",
            reply_markup=get_cancel_keyboard(),
        )
