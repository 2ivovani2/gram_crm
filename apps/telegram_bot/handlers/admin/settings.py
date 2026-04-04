"""
Admin: global settings.
  - referral rate management (global %)
  - set work URL for a specific worker (triggered from user card)
"""
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.referrals.services import ReferralService
from apps.telegram_bot.admin_keyboards import get_settings_keyboard, get_user_card_keyboard, get_admin_main_menu
from apps.telegram_bot.callbacks import AdminMenuCallback, AdminSettingsCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import safe_edit_text
from apps.telegram_bot.states import (
    AdminSetWorkUrlState, AdminSetReferralRateState, AdminSetAttractedCountState,
    AdminSetPersonalRateState, AdminSetReferralRatePerUserState,
)
from apps.users.models import User
from apps.users.services import UserService

router = Router(name="admin_settings")


def _settings_text(ref_settings) -> str:
    rate = ref_settings.rate_percent
    updated = ref_settings.updated_by.display_name if ref_settings.updated_by else "—"
    return (
        "⚙️ <b>Настройки</b>\n\n"
        "<b>Реферальная программа</b>\n"
        f"Ставка: <b>{rate}%</b>\n"
        f"Изменил: {updated}\n\n"
        "<i>Ставка начисляется рефереру с каждого заработка его реферала.</i>"
    )


# ── Settings menu ─────────────────────────────────────────────────────────────

@router.callback_query(AdminMenuCallback.filter(F.section == "settings"), IsAdmin())
async def cb_settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    ref_settings = await sync_to_async(ReferralService.get_settings)()
    await safe_edit_text(callback, _settings_text(ref_settings), get_settings_keyboard())


# ── Referral rate FSM ─────────────────────────────────────────────────────────

@router.callback_query(AdminSettingsCallback.filter(F.action == "set_rate"), IsAdmin())
async def cb_set_rate_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    ref_settings = await sync_to_async(ReferralService.get_settings)()
    await state.set_state(AdminSetReferralRateState.waiting_for_rate)
    from apps.telegram_bot.keyboards import get_cancel_keyboard
    await safe_edit_text(
        callback,
        f"✏️ <b>Изменить реферальную ставку</b>\n\n"
        f"Текущая ставка: <b>{ref_settings.rate_percent}%</b>\n\n"
        "Введите новое значение в процентах (например: <code>5</code> или <code>2.5</code>).\n"
        "Введите <code>0</code> чтобы отключить реферальную программу.",
        get_cancel_keyboard(),
    )


@router.message(AdminSetReferralRateState.waiting_for_rate, IsAdmin())
async def process_set_rate(message: Message, db_user: User, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        rate = float(raw)
        if rate < 0 or rate > 100:
            raise ValueError
    except ValueError:
        from apps.telegram_bot.keyboards import get_cancel_keyboard
        await message.answer("⚠️ Введите число от 0 до 100.", reply_markup=get_cancel_keyboard())
        return

    await state.clear()
    ref_settings = await sync_to_async(ReferralService.set_rate)(rate, db_user)
    await message.answer(
        f"✅ Реферальная ставка установлена: <b>{ref_settings.rate_percent}%</b>",
        reply_markup=get_admin_main_menu(),
    )


# ── Set work URL FSM (triggered from user card) ───────────────────────────────

@router.callback_query(AdminSettingsCallback.filter(F.action == "set_work_url"), IsAdmin())
async def cb_set_work_url_start(callback: CallbackQuery, callback_data: AdminSettingsCallback, state: FSMContext) -> None:
    await callback.answer()
    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    await state.set_state(AdminSetWorkUrlState.waiting_for_url)
    await state.update_data(target_user_id=callback_data.user_id)
    from apps.telegram_bot.keyboards import get_cancel_keyboard
    current = f"\nТекущая: {user.work_url}" if user.work_url else ""
    await safe_edit_text(
        callback,
        f"🔗 <b>Рабочая ссылка для {user.display_name}</b>{current}\n\n"
        "Отправьте новую URL (начинается с https://).\n"
        "Отправьте «-» чтобы удалить ссылку.",
        get_cancel_keyboard(),
    )


@router.message(AdminSetWorkUrlState.waiting_for_url, IsAdmin())
async def process_set_work_url(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target_user_id = data["target_user_id"]
    raw = (message.text or "").strip()
    await state.clear()

    user = await sync_to_async(User.objects.get)(pk=target_user_id)

    if raw == "-":
        await sync_to_async(UserService.set_work_url)(user, "")
        await message.answer(f"✅ Рабочая ссылка для <b>{user.display_name}</b> удалена.", reply_markup=get_admin_main_menu())
        return

    if not (raw.startswith("https://") or raw.startswith("http://")):
        from apps.telegram_bot.keyboards import get_cancel_keyboard
        await state.set_state(AdminSetWorkUrlState.waiting_for_url)
        await state.update_data(target_user_id=target_user_id)
        await message.answer("⚠️ URL должен начинаться с https://. Попробуйте снова.", reply_markup=get_cancel_keyboard())
        return

    user = await sync_to_async(UserService.set_work_url)(user, raw)

    from apps.telegram_bot.admin_keyboards import get_user_card_keyboard as _kb
    await message.answer(
        f"✅ Рабочая ссылка установлена:\n{user.work_url}",
        reply_markup=_kb(user),
    )


# ── Set attracted count FSM ───────────────────────────────────────────────────

@router.callback_query(AdminSettingsCallback.filter(F.action == "set_attracted"), IsAdmin())
async def cb_set_attracted_start(callback: CallbackQuery, callback_data: AdminSettingsCallback, state: FSMContext) -> None:
    await callback.answer()
    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    await state.set_state(AdminSetAttractedCountState.waiting_for_count)
    await state.update_data(target_user_id=callback_data.user_id)
    from apps.telegram_bot.keyboards import get_cancel_keyboard
    await safe_edit_text(
        callback,
        f"👤 <b>Привлечено людей — {user.display_name}</b>\n\n"
        f"Текущее значение: <b>{user.attracted_count}</b>\n\n"
        "Введите новое количество (целое число ≥ 0):",
        get_cancel_keyboard(),
    )


@router.message(AdminSetAttractedCountState.waiting_for_count, IsAdmin())
async def process_set_attracted(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target_user_id = data["target_user_id"]
    raw = (message.text or "").strip()

    try:
        count = int(raw)
        if count < 0:
            raise ValueError
    except ValueError:
        from apps.telegram_bot.keyboards import get_cancel_keyboard
        await message.answer("⚠️ Введите целое число ≥ 0.", reply_markup=get_cancel_keyboard())
        return

    await state.clear()
    user = await sync_to_async(User.objects.get)(pk=target_user_id)
    user = await sync_to_async(UserService.set_attracted_count)(user, count)
    # Refresh user to get updated balance
    user = await sync_to_async(User.objects.get)(pk=target_user_id)

    from apps.telegram_bot.admin_keyboards import get_user_card_keyboard as _kb
    await message.answer(
        f"✅ Привлечено людей для <b>{user.display_name}</b>: <b>{user.attracted_count}</b>\n"
        f"💰 Баланс пересчитан: <b>{user.balance:.2f} ₽</b>",
        reply_markup=_kb(user),
    )


# ── Set personal rate FSM ─────────────────────────────────────────────────────

@router.callback_query(AdminSettingsCallback.filter(F.action == "set_personal_rate"), IsAdmin())
async def cb_set_personal_rate_start(callback: CallbackQuery, callback_data: AdminSettingsCallback, state: FSMContext) -> None:
    await callback.answer()
    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    await state.set_state(AdminSetPersonalRateState.waiting_for_rate)
    await state.update_data(target_user_id=callback_data.user_id)
    from apps.telegram_bot.keyboards import get_cancel_keyboard
    await safe_edit_text(
        callback,
        f"💰 <b>Личная ставка — {user.display_name}</b>\n\n"
        f"Текущая ставка: <b>{user.personal_rate:.2f} руб.</b> за прямого подписчика\n\n"
        "Введите новую ставку в рублях (например: <code>50</code> или <code>12.5</code>):",
        get_cancel_keyboard(),
    )


@router.message(AdminSetPersonalRateState.waiting_for_rate, IsAdmin())
async def process_set_personal_rate(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target_user_id = data["target_user_id"]
    raw = (message.text or "").strip().replace(",", ".")
    try:
        rate = float(raw)
        if rate < 0:
            raise ValueError
    except ValueError:
        from apps.telegram_bot.keyboards import get_cancel_keyboard
        await message.answer("⚠️ Введите число ≥ 0 (например: 50 или 12.5).", reply_markup=get_cancel_keyboard())
        return

    await state.clear()
    user = await sync_to_async(User.objects.get)(pk=target_user_id)
    user = await sync_to_async(UserService.set_personal_rate)(user, rate)
    user = await sync_to_async(User.objects.get)(pk=target_user_id)

    from apps.telegram_bot.admin_keyboards import get_user_card_keyboard as _kb
    await message.answer(
        f"✅ Личная ставка для <b>{user.display_name}</b>: <b>{user.personal_rate:.2f} руб.</b>\n"
        f"💰 Баланс пересчитан: <b>{user.balance:.2f} ₽</b>",
        reply_markup=_kb(user),
    )


# ── Set referral rate per user FSM ────────────────────────────────────────────

@router.callback_query(AdminSettingsCallback.filter(F.action == "set_referral_rate"), IsAdmin())
async def cb_set_referral_rate_start(callback: CallbackQuery, callback_data: AdminSettingsCallback, state: FSMContext) -> None:
    await callback.answer()
    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    await state.set_state(AdminSetReferralRatePerUserState.waiting_for_rate)
    await state.update_data(target_user_id=callback_data.user_id)
    from apps.telegram_bot.keyboards import get_cancel_keyboard
    await safe_edit_text(
        callback,
        f"🤝 <b>Ставка за рефералов — {user.display_name}</b>\n\n"
        f"Текущая ставка: <b>{user.referral_rate:.2f} руб.</b> за подписчика реферала\n\n"
        "Введите новую ставку в рублях (например: <code>10</code> или <code>5.5</code>):",
        get_cancel_keyboard(),
    )


@router.message(AdminSetReferralRatePerUserState.waiting_for_rate, IsAdmin())
async def process_set_referral_rate_per_user(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target_user_id = data["target_user_id"]
    raw = (message.text or "").strip().replace(",", ".")
    try:
        rate = float(raw)
        if rate < 0:
            raise ValueError
    except ValueError:
        from apps.telegram_bot.keyboards import get_cancel_keyboard
        await message.answer("⚠️ Введите число ≥ 0 (например: 10 или 5.5).", reply_markup=get_cancel_keyboard())
        return

    await state.clear()
    user = await sync_to_async(User.objects.get)(pk=target_user_id)
    user = await sync_to_async(UserService.set_referral_rate)(user, rate)
    user = await sync_to_async(User.objects.get)(pk=target_user_id)

    from apps.telegram_bot.admin_keyboards import get_user_card_keyboard as _kb
    await message.answer(
        f"✅ Ставка за рефералов для <b>{user.display_name}</b>: <b>{user.referral_rate:.2f} руб.</b>\n"
        f"💰 Баланс пересчитан: <b>{user.balance:.2f} ₽</b>",
        reply_markup=_kb(user),
    )
