"""
Admin: user management.
  - list with pagination
  - user card view with full earnings breakdown
  - status change, role change
  - free-text search
"""
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.telegram_bot.admin_keyboards import (
    get_users_list_keyboard, get_user_card_keyboard,
    get_user_status_keyboard, get_admin_cancel_keyboard, get_admin_main_menu,
)
from apps.telegram_bot.callbacks import AdminUserCallback
from apps.telegram_bot.services import safe_edit_text
from apps.telegram_bot.states import AdminUserSearchState
from apps.users.models import User
from apps.telegram_bot.permissions import get_bot_access
from apps.users.services import UserService
from apps.common.utils import format_dt

router = Router(name="admin_users")


async def send_users_list(target, page: int = 1) -> None:
    """Entry point for the users list — called from menu.py."""
    from asgiref.sync import sync_to_async
    users, total = await sync_to_async(UserService.get_users_list)(page=page)
    text = f"👥 <b>Пользователи</b>\n\nВсего: {total}"
    if isinstance(target, CallbackQuery):
        await safe_edit_text(target, text, get_users_list_keyboard(users, page=page, total=total))
    else:
        await target.answer(text, reply_markup=get_users_list_keyboard(users, page=page, total=total))


def _user_card_text(user: User, referral_count: int = 0, breakdown: dict | None = None,
                    *, show_finance: bool = False) -> str:
    status_icon = {"active": "✅", "inactive": "⛔", "pending": "⏳", "banned": "🚫"}.get(user.status, "❓")
    role_icon = {"admin": "👑", "curator": "🎓", "worker": "👷", "accountant": "💼", "anonymous": "👤"}.get(user.role, "👤")

    lines = [
        "👤 <b>Карточка пользователя</b>",
        "",
        f"🆔 Telegram ID: <code>{user.telegram_id}</code>",
        f"👤 Имя: <b>{user.display_name}</b>",
    ]
    if user.telegram_username:
        lines.append(f"📱 Username: @{user.telegram_username}")
    lines += [
        "",
        f"{role_icon} Роль: <b>{user.get_role_display()}</b>",
        f"{status_icon} Статус: <b>{user.get_status_display()}</b>",
        f"🔑 Активирован: {'✅ Да' if user.is_activated else '❌ Нет'}",
        f"🤖 Заблокировал бота: {'⚠️ Да' if user.is_blocked_bot else 'Нет'}",
    ]

    if not show_finance:
        lines += ["", f"📅 Зарегистрирован: {format_dt(user.created_at)}",
                  f"⏰ Последняя активность: {format_dt(user.last_activity_at)}"]
        return "\n".join(lines)

    if breakdown:
        lines += [
            "",
            "💼 <b>Начисления</b>",
            f"  👤 Личное:        <b>{breakdown['personal_earned']:.2f} ₽</b>",
            f"  🤝 Реферальное:   <b>{breakdown['referral_earned']:.2f} ₽</b>",
            f"  📅 По ставке:     <b>{breakdown['daily_accrued']:.2f} ₽</b>",
            f"  ✏️ Корректировка: <b>{breakdown['manual_adjustment']:.2f} ₽</b>",
            f"  📊 Начислено:     <b>{breakdown['gross_earned']:.2f} ₽</b>",
            f"  💸 Выведено:      <b>{breakdown['withdrawn']:.2f} ₽</b>",
            f"  ⚠️ Штрафы:        <b>{breakdown['penalties']:.2f} ₽</b>",
            f"  ✅ Баланс:        <b>{breakdown['balance']:.2f} ₽</b>",
        ]
    else:
        lines += [
            "",
            f"💰 Баланс: <b>{user.balance:.2f} ₽</b>",
        ]

    lines += [
        "",
        f"👥 Рефералов: <b>{referral_count}</b>",
        f"💰 Личная ставка: <b>{user.personal_rate:.2f} руб./чел.</b>",
        f"🤝 Ставка за рефералов: <b>{user.referral_rate:.2f} руб./чел.</b>",
    ]

    if breakdown:
        total = breakdown["total_attracted"]
        active = breakdown["active_attracted"]
        archived = total - active
        lines += [
            "",
            "📈 <b>Привлечённые</b>",
            f"  По активной ссылке: <b>{active}</b>",
        ]
        if archived > 0:
            lines.append(f"  Архивные ссылки:    <b>{archived}</b>")
        lines.append(f"  Итого:              <b>{total}</b>")
    else:
        lines.append(f"👤 Привлечено подписчиков: <b>{user.attracted_count}</b>")

    if user.work_url:
        lines.append(f"🔗 Активная ссылка: {user.work_url}")

    lines += [
        "",
        f"📅 Зарегистрирован: {format_dt(user.created_at)}",
        f"⏰ Последняя активность: {format_dt(user.last_activity_at)}",
    ]
    return "\n".join(lines)


def _load_user_data(user_id: int, *, include_finance: bool = False) -> tuple:
    """Load user + referral_count + earnings breakdown in one sync block."""
    from decimal import Decimal
    from apps.control.services import ControlBalanceService, PenaltyService

    user = User.objects.select_related("referred_by").get(pk=user_id)
    referral_count = user.referrals.count()
    if not include_finance:
        return user, referral_count, None
    breakdown = UserService.get_earnings_breakdown(user)
    breakdown.update({
        "daily_accrued": user.daily_accrued or Decimal("0"),
        "manual_adjustment": user.manual_balance_adjustment or Decimal("0"),
        "penalties": PenaltyService.total_accepted_penalty(user),
        "gross_earned": ControlBalanceService.get_total_balance(user),
        "balance": ControlBalanceService.get_available_balance(user),
    })
    return user, referral_count, breakdown


# ── List ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminUserCallback.filter(F.action == "list"))
async def cb_users_list(callback: CallbackQuery, callback_data: AdminUserCallback) -> None:
    await callback.answer()
    page = callback_data.page
    users, total = await sync_to_async(UserService.get_users_list)(page=page)
    text = f"👥 <b>Пользователи</b>\n\nВсего: {total}"
    await safe_edit_text(callback, text, get_users_list_keyboard(users, page=page, total=total))


# ── View ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminUserCallback.filter(F.action == "view"))
async def cb_user_view(callback: CallbackQuery, callback_data: AdminUserCallback, db_user: User) -> None:
    await callback.answer()
    access = await get_bot_access(db_user)
    show_finance = access.allows("employees.finance")
    user, referral_count, breakdown = await sync_to_async(_load_user_data)(
        callback_data.user_id, include_finance=show_finance
    )
    await safe_edit_text(
        callback,
        _user_card_text(user, referral_count, breakdown, show_finance=show_finance),
        get_user_card_keyboard(user, back_page=callback_data.page,
                               can_manage_access=access.allows("access.manage") and user.pk != db_user.pk),
    )


# ── Status change ─────────────────────────────────────────────────────────────

@router.callback_query(AdminUserCallback.filter(F.action == "change_status"))
async def cb_change_status_menu(callback: CallbackQuery, callback_data: AdminUserCallback, db_user: User) -> None:
    if callback_data.user_id == db_user.pk:
        await callback.answer("Нельзя изменять собственный доступ.", show_alert=True)
        return
    await callback.answer()
    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    await safe_edit_text(
        callback,
        f"🔄 Изменить статус для <b>{user.display_name}</b>\n\nТекущий: {user.get_status_display()}",
        get_user_status_keyboard(user),
    )


@router.callback_query(
    AdminUserCallback.filter(F.action.in_({"set_active", "set_inactive", "set_banned"})),
)
async def cb_set_status(callback: CallbackQuery, callback_data: AdminUserCallback, db_user: User) -> None:
    action_map = {"set_active": "active", "set_inactive": "inactive", "set_banned": "banned"}
    new_status = action_map[callback_data.action]

    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    user = await sync_to_async(UserService.set_status)(user, new_status, actor=db_user)
    access = await get_bot_access(db_user)
    show_finance = access.allows("employees.finance")
    user, referral_count, breakdown = await sync_to_async(_load_user_data)(
        user.pk, include_finance=show_finance
    )

    await callback.answer(f"Статус → {user.get_status_display()}", show_alert=True)
    await safe_edit_text(
        callback,
        _user_card_text(user, referral_count, breakdown, show_finance=show_finance),
        get_user_card_keyboard(user, back_page=1,
                               can_manage_access=access.allows("access.manage") and user.pk != db_user.pk),
    )


# ── Search ────────────────────────────────────────────────────────────────────

@router.callback_query(AdminUserCallback.filter(F.action == "search"))
async def cb_user_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(AdminUserSearchState.waiting_for_query)
    await safe_edit_text(
        callback,
        "🔍 <b>Поиск пользователя</b>\n\nВведите Telegram ID, имя или @username:",
        get_admin_cancel_keyboard("users"),
    )


@router.message(AdminUserSearchState.waiting_for_query)
async def process_user_search(message: Message, state: FSMContext, db_user: User) -> None:
    await state.clear()
    query = (message.text or "").strip()
    users = await sync_to_async(UserService.search_users)(query)

    if not users:
        await message.answer(
            f"🔍 По запросу «{query}» ничего не найдено.",
            reply_markup=get_admin_main_menu(await get_bot_access(db_user)),
        )
        return

    text = f"🔍 Найдено: {len(users)}"
    await message.answer(text, reply_markup=get_users_list_keyboard(users, page=1, total=len(users)))


# ── Role assignment ───────────────────────────────────────────────────────────

@router.callback_query(
    AdminUserCallback.filter(F.action.in_({"set_curator", "set_worker"})),
)
async def cb_set_role(callback: CallbackQuery, callback_data: AdminUserCallback) -> None:
    # Legacy buttons cannot skip version checks and role-change confirmation.
    from django.conf import settings
    from urllib.parse import urljoin
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    await callback.answer("Смена роли перенесена в форму доступа CRM.", show_alert=True)
    await callback.message.answer(
        "🔐 Чтобы изменить роль, откройте сотрудника в CRM → Доступ. "
        "Форма покажет новые права и попросит подтвердить изменение.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть управление доступом", url=urljoin(
                getattr(settings, "CRM_URL", "https://crm.gramly.tech/crm/login/"),
                "/crm/control/users/",
            ))
        ]]),
    )


# ── Noop (pagination label tap) ───────────────────────────────────────────────

@router.callback_query(AdminUserCallback.filter(F.action == "noop"))
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()
