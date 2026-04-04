"""
Admin: user management.
  - list with pagination
  - user card view
  - status change
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
from apps.telegram_bot.callbacks import AdminMenuCallback, AdminUserCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import answer_and_edit, safe_edit_text
from apps.telegram_bot.states import AdminUserSearchState
from apps.users.models import User
from apps.users.services import UserService
from apps.common.utils import format_dt

router = Router(name="admin_users")


def _user_card_text(user: User, referral_count: int = 0) -> str:
    status_icon = {"active": "✅", "inactive": "⛔", "pending": "⏳", "banned": "🚫"}.get(user.status, "❓")
    role_icon = {"admin": "👑", "worker": "👷"}.get(user.role, "❓")

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
        "",
        f"💰 Баланс: <b>{user.balance:.2f} ₽</b>",
        f"👥 Рефералов: <b>{referral_count}</b>",
        f"👤 Привлечено подписчиков: <b>{user.attracted_count}</b>",
        f"💰 Личная ставка: <b>{user.personal_rate:.2f} руб./чел.</b>",
        f"🤝 Ставка за рефералов: <b>{user.referral_rate:.2f} руб./чел.</b>",
    ]
    if user.work_url:
        lines.append(f"🔗 Рабочая ссылка: {user.work_url}")
    lines += [
        "",
        f"📅 Зарегистрирован: {format_dt(user.created_at)}",
        f"⏰ Последняя активность: {format_dt(user.last_activity_at)}",
    ]
    return "\n".join(lines)


# ── List ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminMenuCallback.filter(F.section == "users"), IsAdmin())
async def cb_users_section(callback: CallbackQuery, db_user: User) -> None:
    await callback.answer()
    users, total = await sync_to_async(UserService.get_users_list)(page=1)
    text = f"👥 <b>Пользователи</b>\n\nВсего: {total}"
    await safe_edit_text(callback, text, get_users_list_keyboard(users, page=1, total=total))


@router.callback_query(AdminUserCallback.filter(F.action == "list"), IsAdmin())
async def cb_users_list(callback: CallbackQuery, callback_data: AdminUserCallback) -> None:
    await callback.answer()
    page = callback_data.page
    users, total = await sync_to_async(UserService.get_users_list)(page=page)
    text = f"👥 <b>Пользователи</b>\n\nВсего: {total}"
    await safe_edit_text(callback, text, get_users_list_keyboard(users, page=page, total=total))


# ── View ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminUserCallback.filter(F.action == "view"), IsAdmin())
async def cb_user_view(callback: CallbackQuery, callback_data: AdminUserCallback) -> None:
    await callback.answer()
    user, referral_count = await sync_to_async(
        lambda: (
            User.objects.select_related("referred_by").get(pk=callback_data.user_id),
            User.objects.get(pk=callback_data.user_id).referrals.count(),
        )
    )()
    await safe_edit_text(callback, _user_card_text(user, referral_count), get_user_card_keyboard(user, back_page=callback_data.page))


# ── Status change ─────────────────────────────────────────────────────────────

@router.callback_query(AdminUserCallback.filter(F.action == "change_status"), IsAdmin())
async def cb_change_status_menu(callback: CallbackQuery, callback_data: AdminUserCallback) -> None:
    await callback.answer()
    user = await sync_to_async(User.objects.get)(pk=callback_data.user_id)
    await safe_edit_text(
        callback,
        f"🔄 Изменить статус для <b>{user.display_name}</b>\n\nТекущий: {user.get_status_display()}",
        get_user_status_keyboard(user),
    )


@router.callback_query(
    AdminUserCallback.filter(F.action.in_({"set_active", "set_inactive", "set_banned"})),
    IsAdmin(),
)
async def cb_set_status(callback: CallbackQuery, callback_data: AdminUserCallback) -> None:
    action_map = {"set_active": "active", "set_inactive": "inactive", "set_banned": "banned"}
    new_status = action_map[callback_data.action]

    user, referral_count = await sync_to_async(
        lambda: (
            User.objects.get(pk=callback_data.user_id),
            User.objects.get(pk=callback_data.user_id).referrals.count(),
        )
    )()
    user = await sync_to_async(UserService.set_status)(user, new_status)

    await callback.answer(f"Статус → {user.get_status_display()}", show_alert=True)
    await safe_edit_text(callback, _user_card_text(user, referral_count), get_user_card_keyboard(user, back_page=1))


# ── Search ────────────────────────────────────────────────────────────────────

@router.callback_query(AdminUserCallback.filter(F.action == "search"), IsAdmin())
async def cb_user_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(AdminUserSearchState.waiting_for_query)
    await safe_edit_text(
        callback,
        "🔍 <b>Поиск пользователя</b>\n\nВведите Telegram ID, имя или @username:",
        get_admin_cancel_keyboard("users"),
    )


@router.message(AdminUserSearchState.waiting_for_query, IsAdmin())
async def process_user_search(message: Message, state: FSMContext) -> None:
    await state.clear()
    query = (message.text or "").strip()
    users = await sync_to_async(UserService.search_users)(query)

    if not users:
        await message.answer(
            f"🔍 По запросу «{query}» ничего не найдено.",
            reply_markup=get_admin_main_menu(),
        )
        return

    text = f"🔍 Найдено: {len(users)}"
    await message.answer(text, reply_markup=get_users_list_keyboard(users, page=1, total=len(users)))


# ── Noop (pagination label tap) ───────────────────────────────────────────────

@router.callback_query(AdminUserCallback.filter(F.action == "noop"), IsAdmin())
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()
