"""Admin-facing inline keyboards — minimal set for user management."""
from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from .callbacks import AdminMenuCallback, AdminUserCallback, AdminSettingsCallback

PAGE_SIZE = 10


def _main_btn() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="🔙 Главное меню", callback_data=AdminMenuCallback(section="main").pack())


def _add_pagination_users(b: InlineKeyboardBuilder, page: int, total: int) -> None:
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    row = []
    if page > 1:
        row.append(InlineKeyboardButton(
            text="◀️", callback_data=AdminUserCallback(action="list", user_id=0, page=page - 1).pack()
        ))
    row.append(InlineKeyboardButton(
        text=f"· {page}/{total_pages} ·",
        callback_data=AdminUserCallback(action="noop", user_id=0, page=page).pack(),
    ))
    if page < total_pages:
        row.append(InlineKeyboardButton(
            text="▶️", callback_data=AdminUserCallback(action="list", user_id=0, page=page + 1).pack()
        ))
    if row:
        b.row(*row)


# ── Main Menu ─────────────────────────────────────────────────────────────────

def get_admin_main_menu() -> InlineKeyboardMarkup:
    from django.conf import settings
    from apps.control.bot.keyboards import CtrlAdminCB
    crm_url  = getattr(settings, "CRM_URL",  "https://gramly.tech/crm/login/")
    docs_url = getattr(settings, "DOCS_URL", "https://gramly.tech/docs/")

    b = InlineKeyboardBuilder()
    # ── Control bot ───────────────────────────────────────────────────────────
    b.button(text="📋 Отчёты на проверке", callback_data=CtrlAdminCB(action="reports"))
    b.button(text="⚠️ Штрафы", callback_data=CtrlAdminCB(action="penalties"))
    b.button(text="📢 Рассылка", callback_data=CtrlAdminCB(action="broadcast"))
    b.button(text="➕ Создать штраф", callback_data=CtrlAdminCB(action="create_penalty"))
    b.button(text="💸 Вывод средств", callback_data=CtrlAdminCB(action="withdraw"))
    b.button(text="💳 Мои адреса", callback_data=CtrlAdminCB(action="my_addresses"))
    # ── Management ────────────────────────────────────────────────────────────
    b.button(text="👥 Пользователи", callback_data=AdminMenuCallback(section="users"))
    b.button(text="📊 CRM", url=crm_url)
    b.button(text="📖 Документация", url=docs_url)
    b.adjust(1)
    return b.as_markup()


def get_admin_cancel_keyboard(back_section: str = "main") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data=AdminMenuCallback(section=back_section))
    b.adjust(1)
    return b.as_markup()


# ── Users ─────────────────────────────────────────────────────────────────────

_STATUS_ICONS = {"active": "✅", "pending": "⏳", "inactive": "⛔", "banned": "🚫"}
_ROLE_ICONS = {"admin": "👑", "curator": "🎓", "worker": "👷"}


def get_users_list_keyboard(users, page: int, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for user in users:
        icon = _STATUS_ICONS.get(user.status, "❓")
        role_icon = _ROLE_ICONS.get(user.role, "")
        b.button(
            text=f"{icon}{role_icon} {user.display_name}",
            callback_data=AdminUserCallback(action="view", user_id=user.id, page=page),
        )
    b.adjust(1)
    _add_pagination_users(b, page, total)
    b.row(
        InlineKeyboardButton(text="🔍 Поиск", callback_data=AdminUserCallback(action="search", user_id=0, page=1).pack()),
        _main_btn(),
    )
    return b.as_markup()


def get_user_card_keyboard(user, back_page: int = 1) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Изменить статус", callback_data=AdminUserCallback(action="change_status", user_id=user.id, page=back_page))
    b.button(text="🔙 К списку", callback_data=AdminUserCallback(action="list", user_id=0, page=back_page))
    b.adjust(1)
    return b.as_markup()


def get_user_status_keyboard(user) -> InlineKeyboardMarkup:
    from apps.users.models import UserStatus
    b = InlineKeyboardBuilder()
    options = [
        (UserStatus.ACTIVE, "✅ Активный"),
        (UserStatus.INACTIVE, "⛔ Неактивный"),
        (UserStatus.BANNED, "🚫 Забанен"),
    ]
    for val, label in options:
        marker = "● " if user.status == val else ""
        b.button(
            text=f"{marker}{label}",
            callback_data=AdminUserCallback(action=f"set_{val}", user_id=user.id, page=1),
        )
    b.button(text="🔙 Назад", callback_data=AdminUserCallback(action="view", user_id=user.id, page=1))
    b.adjust(1)
    return b.as_markup()


def get_settings_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Главное меню", callback_data=AdminMenuCallback(section="main"))
    b.adjust(1)
    return b.as_markup()
