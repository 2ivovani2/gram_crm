"""
Admin: settings panel.
  - RateConfig (worker_share / referral_share)
  - Set attracted_count (on active WorkLink)
  - Replace work link (archive old, create new with count=0)
  - Set personal rate / referral rate per user
"""
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from apps.telegram_bot.admin_keyboards import (
    get_settings_keyboard, get_user_card_keyboard, get_admin_main_menu,
    get_rate_config_cancel_keyboard,
)
from apps.telegram_bot.callbacks import AdminMenuCallback, AdminSettingsCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import safe_edit_text
from apps.telegram_bot.states import (
    AdminSetAttractedCountState,
    AdminSetPersonalRateState, AdminSetReferralRatePerUserState,
    AdminSetRateConfigState, AdminReplaceWorkLinkState,
)
from apps.users.models import User
from apps.users.services import UserService

router = Router(name="admin_settings")


def _settings_text(config) -> str:
    return (
        "⚙️ <b>Настройки</b>\n\n"
        "<b>Доли ставок (для дневного отчёта)</b>\n"
        f"  Доля работника:  <b>{float(config.worker_share)*100:.2f}%</b> от ставки клиента\n"
        f"  Доля реферала:   <b>{float(config.referral_share)*100:.2f}%</b> от ставки клиента\n"
        f"  Наша прибыль:    <b>{(1 - float(config.worker_share) - float(config.referral_share))*100:.2f}%</b>\n\n"
        "<i>Эти доли используются при расчёте ставок в форме «Ввод данных».</i>"
    )


def _get_replace_link_confirm_keyboard(user_id: int) -> InlineKeyboardMarkup:
    from apps.telegram_bot.callbacks import AdminSettingsCallback
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить замену",
                callback_data=AdminSettingsCallback(action="replace_link_do", user_id=user_id).pack(),
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=AdminSettingsCallback(action="replace_link_cancel", user_id=user_id).pack(),
            ),
        ]
    ])


def _get_cancel_keyboard() -> InlineKeyboardMarkup:
    from apps.telegram_bot.keyboards import get_cancel_keyboard
    return get_cancel_keyboard()


# ── Settings menu ─────────────────────────────────────────────────────────────

@router.callback_query(AdminMenuCallback.filter(F.section == "settings"), IsAdmin())
async def cb_settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    from apps.stats.models import RateConfig
    config = await sync_to_async(RateConfig.get)()
    await safe_edit_text(callback, _settings_text(config), get_settings_keyboard())


@router.callback_query(AdminSettingsCallback.filter(F.action == "set_rate"), IsAdmin())
async def cb_set_rate_legacy(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Глобальная % ставка больше не используется.", show_alert=True)
    from apps.stats.models import RateConfig
    config = await sync_to_async(RateConfig.get)()
    await safe_edit_text(callback, _settings_text(config), get_settings_keyboard())


# ── RateConfig FSM ────────────────────────────────────────────────────────────

@router.callback_query(AdminSettingsCallback.filter(F.action == "set_rate_config"), IsAdmin())
async def cb_set_rate_config_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    from apps.stats.models import RateConfig
    config = await sync_to_async(RateConfig.get)()
    await state.set_state(AdminSetRateConfigState.waiting_for_worker_share)
    await safe_edit_text(
        callback,
        f"⚙️ <b>Доля работника</b>\n\n"
        f"Текущая: <b>{float(config.worker_share)*100:.2f}%</b>\n\n"
        "Введите новое значение в процентах (например: <code>25</code> для 25%):",
        get_rate_config_cancel_keyboard(),
    )


@router.message(AdminSetRateConfigState.waiting_for_worker_share, IsAdmin())
async def process_worker_share(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        val = Decimal(raw)
        if val < 0 or val > 100:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ Введите число от 0 до 100.", reply_markup=get_rate_config_cancel_keyboard())
        return
    await state.update_data(worker_share=str(val / 100))
    await state.set_state(AdminSetRateConfigState.waiting_for_referral_share)
    from apps.stats.models import RateConfig
    config = await sync_to_async(RateConfig.get)()
    await message.answer(
        f"⚙️ <b>Доля реферала</b>\n\n"
        f"Текущая: <b>{float(config.referral_share)*100:.2f}%</b>\n\n"
        "Введите новое значение в процентах (например: <code>13.89</code>):",
        reply_markup=get_rate_config_cancel_keyboard(),
    )


@router.message(AdminSetRateConfigState.waiting_for_referral_share, IsAdmin())
async def process_referral_share(message: Message, db_user: User, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        val = Decimal(raw)
        if val < 0 or val > 100:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ Введите число от 0 до 100.", reply_markup=get_rate_config_cancel_keyboard())
        return

    data = await state.get_data()
    await state.clear()

    worker_share = Decimal(data["worker_share"])
    referral_share = val / 100
    our_share = 1 - worker_share - referral_share

    if our_share < 0:
        await message.answer("⚠️ Сумма долей больше 100%. Начните заново.", reply_markup=get_admin_main_menu())
        return

    from apps.stats.models import RateConfig
    config = await sync_to_async(RateConfig.get)()
    config.worker_share = worker_share
    config.referral_share = referral_share
    config.updated_by = db_user
    await sync_to_async(config.save)(update_fields=["worker_share", "referral_share", "updated_by", "updated_at"])

    await message.answer(
        f"✅ Доли обновлены!\n\n"
        f"  Работник: <b>{float(worker_share)*100:.2f}%</b>\n"
        f"  Реферал:  <b>{float(referral_share)*100:.2f}%</b>\n"
        f"  Прибыль:  <b>{float(our_share)*100:.2f}%</b>",
        reply_markup=get_admin_main_menu(),
    )


# ── Replace work link FSM ─────────────────────────────────────────────────────
# Flow: start → show current state (count/url) → enter new URL → confirm → done
# Old link is ARCHIVED (attracted_count frozen), new link starts at 0.

@router.callback_query(AdminSettingsCallback.filter(F.action == "set_work_url"), IsAdmin())
async def cb_replace_work_link_start(callback: CallbackQuery, callback_data: AdminSettingsCallback, state: FSMContext) -> None:
    """
    Renamed from 'set_work_url' — now does a full link REPLACEMENT.
    Shows current state: active link URL + attracted_count + archived count.
    Warns that counter resets to 0 for new link (old balance preserved).
    """
    await callback.answer()
    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    history = await sync_to_async(UserService.get_work_link_history)(user)
    active = next((l for l in history if l.is_active), None)
    archived = [l for l in history if not l.is_active]

    await state.set_state(AdminReplaceWorkLinkState.waiting_for_new_url)
    await state.update_data(target_user_id=callback_data.user_id)

    lines = [f"🔗 <b>Замена рабочей ссылки — {user.display_name}</b>", ""]
    if active:
        lines += [
            "📌 <b>Активная ссылка сейчас:</b>",
            f"  URL: <code>{active.url or '(не задана)'}</code>",
            f"  Привлечено по ней: <b>{active.attracted_count}</b> чел.",
        ]
    else:
        lines.append("⚠️ Активной ссылки нет.")

    if archived:
        archived_total = sum(l.attracted_count for l in archived)
        lines += [
            "",
            f"📂 Архивных ссылок: <b>{len(archived)}</b> (итого привлечено: {archived_total} чел.)",
        ]

    lines += [
        "",
        "⚠️ <b>При замене:</b>",
        "  • Текущая ссылка перейдёт в архив",
        "  • Её счётчик зафиксируется навсегда",
        "  • Новая ссылка стартует с <b>0</b>",
        "  • Старые начисления <b>сохраняются</b>",
        "",
        "Введите новый URL (https://...) или «-» для удаления активной ссылки:",
    ]
    await safe_edit_text(callback, "\n".join(lines), _get_cancel_keyboard())


@router.message(AdminReplaceWorkLinkState.waiting_for_new_url, IsAdmin())
async def process_replace_work_link_url(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target_user_id = data["target_user_id"]
    raw = (message.text or "").strip()

    if raw != "-" and not (raw.startswith("https://") or raw.startswith("http://")):
        await message.answer(
            "⚠️ URL должен начинаться с https://. Введите URL или «-» для удаления.",
            reply_markup=_get_cancel_keyboard(),
        )
        return

    await state.update_data(new_url=raw)
    await state.set_state(AdminReplaceWorkLinkState.confirm)

    user = await sync_to_async(User.objects.get)(pk=target_user_id)
    active = await sync_to_async(
        lambda: user.work_links.filter(is_active=True).first()
    )()

    if raw == "-":
        action_text = "Активная ссылка будет удалена (перейдёт в архив с текущим счётчиком)."
    else:
        action_text = f"Новая ссылка:\n<code>{raw}</code>"

    old_count = active.attracted_count if active else 0
    confirm_text = (
        f"⚠️ <b>Подтвердите замену ссылки для {user.display_name}</b>\n\n"
        f"Старая ссылка → архив (зафиксировано: <b>{old_count}</b> чел.)\n"
        f"{action_text}\n"
        f"Новый счётчик стартует с <b>0</b>.\n\n"
        f"Ранее начисленное за {old_count} чел. <b>НЕ пропадёт</b>."
    )
    await message.answer(confirm_text, reply_markup=_get_replace_link_confirm_keyboard(target_user_id))


@router.callback_query(AdminSettingsCallback.filter(F.action == "replace_link_do"), IsAdmin())
async def cb_replace_link_confirm(callback: CallbackQuery, callback_data: AdminSettingsCallback, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    new_url = data.get("new_url", "")
    await state.clear()

    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    new_link, old_link = await sync_to_async(UserService.replace_work_link)(
        user, "" if new_url == "-" else new_url
    )
    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    breakdown = await sync_to_async(UserService.get_earnings_breakdown)(user)

    old_count = old_link.attracted_count if old_link else 0
    await callback.message.answer(
        f"✅ <b>Ссылка заменена для {user.display_name}</b>\n\n"
        f"Архивная ссылка: <b>{old_count}</b> чел. зафиксировано\n"
        f"Новая ссылка: счётчик = <b>0</b>\n"
        f"💰 Баланс: <b>{breakdown['balance']:.2f} ₽</b> (начислено: {breakdown['gross_earned']:.2f} ₽, выведено: {breakdown['withdrawn']:.2f} ₽)",
        reply_markup=get_user_card_keyboard(user),
    )


@router.callback_query(AdminSettingsCallback.filter(F.action == "replace_link_cancel"), IsAdmin())
async def cb_replace_link_cancel(callback: CallbackQuery, callback_data: AdminSettingsCallback, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Замена отменена.")
    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    await callback.message.answer(
        f"Замена ссылки для <b>{user.display_name}</b> отменена.",
        reply_markup=get_user_card_keyboard(user),
    )


# ── Set attracted count FSM ───────────────────────────────────────────────────

@router.callback_query(AdminSettingsCallback.filter(F.action == "set_attracted"), IsAdmin())
async def cb_set_attracted_start(callback: CallbackQuery, callback_data: AdminSettingsCallback, state: FSMContext) -> None:
    await callback.answer()
    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    active = await sync_to_async(
        lambda: user.work_links.filter(is_active=True).first()
    )()
    active_count = active.attracted_count if active else user.attracted_count
    await state.set_state(AdminSetAttractedCountState.waiting_for_count)
    await state.update_data(target_user_id=callback_data.user_id)
    await safe_edit_text(
        callback,
        f"👤 <b>Привлечено по активной ссылке — {user.display_name}</b>\n\n"
        f"Текущее значение (активная ссылка): <b>{active_count}</b>\n\n"
        "⚠️ Вводите <b>только число для активной ссылки</b>.\n"
        "Архивные ссылки не затрагиваются.\n\n"
        "Введите новое количество (целое число ≥ 0):",
        _get_cancel_keyboard(),
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
        await message.answer("⚠️ Введите целое число ≥ 0.", reply_markup=_get_cancel_keyboard())
        return

    await state.clear()
    user = await sync_to_async(User.objects.get)(pk=target_user_id)
    user = await sync_to_async(UserService.set_attracted_count)(user, count)
    breakdown = await sync_to_async(UserService.get_earnings_breakdown)(user)

    await message.answer(
        f"✅ Привлечено (активная ссылка) для <b>{user.display_name}</b>: <b>{count}</b>\n"
        f"💼 Личное начисление:  <b>{breakdown['personal_earned']:.2f} ₽</b>\n"
        f"🤝 Реферальное:        <b>{breakdown['referral_earned']:.2f} ₽</b>\n"
        f"💰 Баланс:             <b>{breakdown['balance']:.2f} ₽</b>",
        reply_markup=get_user_card_keyboard(user),
    )


# ── Set personal rate FSM ─────────────────────────────────────────────────────

@router.callback_query(AdminSettingsCallback.filter(F.action == "set_personal_rate"), IsAdmin())
async def cb_set_personal_rate_start(callback: CallbackQuery, callback_data: AdminSettingsCallback, state: FSMContext) -> None:
    await callback.answer()
    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    breakdown = await sync_to_async(UserService.get_earnings_breakdown)(user)
    await state.set_state(AdminSetPersonalRateState.waiting_for_rate)
    await state.update_data(target_user_id=callback_data.user_id)
    await safe_edit_text(
        callback,
        f"💰 <b>Личная ставка — {user.display_name}</b>\n\n"
        f"Текущая: <b>{user.personal_rate:.2f} руб./чел.</b>\n"
        f"Итого привлечено: <b>{breakdown['total_attracted']}</b> чел.\n"
        f"Текущее личное начисление: <b>{breakdown['personal_earned']:.2f} ₽</b>\n\n"
        "Введите новую ставку в рублях (например: <code>50</code> или <code>12.5</code>):",
        _get_cancel_keyboard(),
    )


@router.message(AdminSetPersonalRateState.waiting_for_rate, IsAdmin())
async def process_set_personal_rate(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target_user_id = data["target_user_id"]
    raw = (message.text or "").strip().replace(",", ".")
    try:
        rate = Decimal(raw)
        if rate < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ Введите число ≥ 0 (например: 50 или 12.5).", reply_markup=_get_cancel_keyboard())
        return

    await state.clear()
    user = await sync_to_async(User.objects.get)(pk=target_user_id)
    user = await sync_to_async(UserService.set_personal_rate)(user, rate)
    breakdown = await sync_to_async(UserService.get_earnings_breakdown)(user)

    await message.answer(
        f"✅ Личная ставка для <b>{user.display_name}</b>: <b>{rate:.2f} руб./чел.</b>\n\n"
        f"Итого привлечено: <b>{breakdown['total_attracted']}</b> чел.\n"
        f"💼 Личное начисление: <b>{breakdown['personal_earned']:.2f} ₽</b>\n"
        f"🤝 Реферальное:      <b>{breakdown['referral_earned']:.2f} ₽</b>\n"
        f"💰 Баланс:           <b>{breakdown['balance']:.2f} ₽</b>",
        reply_markup=get_user_card_keyboard(user),
    )


# ── Set referral rate per user FSM ────────────────────────────────────────────

@router.callback_query(AdminSettingsCallback.filter(F.action == "set_referral_rate"), IsAdmin())
async def cb_set_referral_rate_start(callback: CallbackQuery, callback_data: AdminSettingsCallback, state: FSMContext) -> None:
    await callback.answer()
    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    breakdown = await sync_to_async(UserService.get_earnings_breakdown)(user)
    await state.set_state(AdminSetReferralRatePerUserState.waiting_for_rate)
    await state.update_data(target_user_id=callback_data.user_id)
    await safe_edit_text(
        callback,
        f"🤝 <b>Ставка за рефералов — {user.display_name}</b>\n\n"
        f"Текущая: <b>{user.referral_rate:.2f} руб./чел.</b>\n"
        f"Рефералы привлекли: <b>{breakdown['referrals_total_attracted']}</b> чел.\n"
        f"Текущее реф. начисление: <b>{breakdown['referral_earned']:.2f} ₽</b>\n\n"
        "Введите новую ставку в рублях (например: <code>10</code> или <code>5.5</code>):",
        _get_cancel_keyboard(),
    )


@router.message(AdminSetReferralRatePerUserState.waiting_for_rate, IsAdmin())
async def process_set_referral_rate_per_user(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target_user_id = data["target_user_id"]
    raw = (message.text or "").strip().replace(",", ".")
    try:
        rate = Decimal(raw)
        if rate < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ Введите число ≥ 0 (например: 10 или 5.5).", reply_markup=_get_cancel_keyboard())
        return

    await state.clear()
    user = await sync_to_async(User.objects.get)(pk=target_user_id)
    user = await sync_to_async(UserService.set_referral_rate)(user, rate)
    breakdown = await sync_to_async(UserService.get_earnings_breakdown)(user)

    await message.answer(
        f"✅ Ставка за рефералов для <b>{user.display_name}</b>: <b>{rate:.2f} руб./чел.</b>\n\n"
        f"Рефералы привлекли: <b>{breakdown['referrals_total_attracted']}</b> чел.\n"
        f"🤝 Реферальное начисление: <b>{breakdown['referral_earned']:.2f} ₽</b>\n"
        f"💰 Баланс:                 <b>{breakdown['balance']:.2f} ₽</b>",
        reply_markup=get_user_card_keyboard(user),
    )
