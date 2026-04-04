"""Admin-facing inline keyboards."""
from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from .callbacks import (
    AdminMenuCallback, AdminUserCallback, AdminInviteCallback,
    AdminBroadcastCallback, AdminStatsCallback, AdminSettingsCallback,
    AdminWithdrawalCallback,
)

PAGE_SIZE = 10

# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _add_pagination_invites(b: InlineKeyboardBuilder, page: int, total: int) -> None:
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    row = []
    if page > 1:
        row.append(InlineKeyboardButton(
            text="◀️", callback_data=AdminInviteCallback(action="list", key_id=0, page=page - 1).pack()
        ))
    row.append(InlineKeyboardButton(
        text=f"· {page}/{total_pages} ·",
        callback_data=AdminInviteCallback(action="noop", key_id=0, page=page).pack(),
    ))
    if page < total_pages:
        row.append(InlineKeyboardButton(
            text="▶️", callback_data=AdminInviteCallback(action="list", key_id=0, page=page + 1).pack()
        ))
    if row:
        b.row(*row)


def _add_pagination_broadcasts(b: InlineKeyboardBuilder, page: int, total: int) -> None:
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    row = []
    if page > 1:
        row.append(InlineKeyboardButton(
            text="◀️", callback_data=AdminBroadcastCallback(action="list", broadcast_id=0, page=page - 1).pack()
        ))
    row.append(InlineKeyboardButton(
        text=f"· {page}/{total_pages} ·",
        callback_data=AdminBroadcastCallback(action="noop", broadcast_id=0, page=page).pack(),
    ))
    if page < total_pages:
        row.append(InlineKeyboardButton(
            text="▶️", callback_data=AdminBroadcastCallback(action="list", broadcast_id=0, page=page + 1).pack()
        ))
    if row:
        b.row(*row)


def _add_pagination_logs(b: InlineKeyboardBuilder, page: int, total: int, broadcast_id: int, page_size: int = 15) -> None:
    total_pages = max(1, (total + page_size - 1) // page_size)
    row = []
    if page > 1:
        row.append(InlineKeyboardButton(
            text="◀️", callback_data=AdminBroadcastCallback(action="logs", broadcast_id=broadcast_id, page=page - 1).pack()
        ))
    row.append(InlineKeyboardButton(
        text=f"· {page}/{total_pages} ·",
        callback_data=AdminBroadcastCallback(action="noop", broadcast_id=broadcast_id, page=page).pack(),
    ))
    if page < total_pages:
        row.append(InlineKeyboardButton(
            text="▶️", callback_data=AdminBroadcastCallback(action="logs", broadcast_id=broadcast_id, page=page + 1).pack()
        ))
    if row:
        b.row(*row)


# ── Main Menu ─────────────────────────────────────────────────────────────────

def get_admin_main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="👥 Пользователи", callback_data=AdminMenuCallback(section="users"))
    b.button(text="🔑 Invite Keys", callback_data=AdminMenuCallback(section="invites"))
    b.button(text="📢 Рассылки", callback_data=AdminMenuCallback(section="broadcasts"))
    b.button(text="📊 Статистика", callback_data=AdminMenuCallback(section="stats"))
    b.button(text="💸 Выводы", callback_data=AdminMenuCallback(section="withdrawals"))
    b.button(text="⚙️ Настройки", callback_data=AdminMenuCallback(section="settings"))
    b.adjust(2, 2, 2)
    return b.as_markup()


def get_admin_cancel_keyboard(back_section: str = "main") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data=AdminMenuCallback(section=back_section))
    b.adjust(1)
    return b.as_markup()


# ── Users ─────────────────────────────────────────────────────────────────────

_STATUS_ICONS = {"active": "✅", "pending": "⏳", "inactive": "⛔", "banned": "🚫"}


def get_users_list_keyboard(users, page: int, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for user in users:
        icon = _STATUS_ICONS.get(user.status, "❓")
        b.button(
            text=f"{icon} {user.display_name}",
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
    b.button(text="🔗 Рабочая ссылка", callback_data=AdminSettingsCallback(action="set_work_url", user_id=user.id))
    b.button(text="👤 Привлечено людей", callback_data=AdminSettingsCallback(action="set_attracted", user_id=user.id))
    b.button(text="💰 Личная ставка", callback_data=AdminSettingsCallback(action="set_personal_rate", user_id=user.id))
    b.button(text="🤝 Ставка за рефералов", callback_data=AdminSettingsCallback(action="set_referral_rate", user_id=user.id))
    b.button(text="🔙 К списку", callback_data=AdminUserCallback(action="list", user_id=0, page=back_page))
    b.adjust(1)
    return b.as_markup()


def get_settings_keyboard(back_to_url_user_id: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Изменить реф. ставку", callback_data=AdminSettingsCallback(action="set_rate"))
    b.button(text="🔙 Главное меню", callback_data=AdminMenuCallback(section="main"))
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


# ── Invite Keys ───────────────────────────────────────────────────────────────

def get_invites_list_keyboard(keys, page: int, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key in keys:
        icon = "✅" if key.is_valid else "❌"
        uses = f"{key.uses_count}/{key.max_uses or '∞'}"
        label = f"{key.key[:10]}… [{uses}]"
        b.button(
            text=f"{icon} {label}",
            callback_data=AdminInviteCallback(action="view", key_id=key.id, page=page),
        )
    b.adjust(1)
    _add_pagination_invites(b, page, total)
    b.row(
        InlineKeyboardButton(text="➕ Создать", callback_data=AdminInviteCallback(action="create", key_id=0, page=1).pack()),
        _main_btn(),
    )
    return b.as_markup()


def get_invite_key_card_keyboard(key, back_page: int = 1) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    toggle_label = "🔴 Деактивировать" if key.is_active else "🟢 Активировать"
    b.button(text=toggle_label, callback_data=AdminInviteCallback(action="toggle", key_id=key.id, page=back_page))
    b.button(text="📋 Активации", callback_data=AdminInviteCallback(action="activations", key_id=key.id, page=1))
    b.button(text="🔙 К списку", callback_data=AdminInviteCallback(action="list", key_id=0, page=back_page))
    b.adjust(1)
    return b.as_markup()


def get_invite_activations_keyboard(key_id: int, page: int, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    # reuse broadcast pagination shape adapted for invite activations
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    row = []
    if page > 1:
        row.append(InlineKeyboardButton(
            text="◀️", callback_data=AdminInviteCallback(action="activations", key_id=key_id, page=page - 1).pack()
        ))
    row.append(InlineKeyboardButton(text=f"· {page}/{total_pages} ·", callback_data=AdminInviteCallback(action="noop", key_id=key_id, page=page).pack()))
    if page < total_pages:
        row.append(InlineKeyboardButton(
            text="▶️", callback_data=AdminInviteCallback(action="activations", key_id=key_id, page=page + 1).pack()
        ))
    if row:
        b.row(*row)
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data=AdminInviteCallback(action="view", key_id=key_id, page=1).pack()))
    return b.as_markup()


# ── Broadcasts ────────────────────────────────────────────────────────────────

_BC_STATUS_ICONS = {"draft": "📝", "confirmed": "✅", "running": "🔄", "done": "✔️", "failed": "❌"}


def get_broadcasts_list_keyboard(broadcasts, page: int, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for bc in broadcasts:
        icon = _BC_STATUS_ICONS.get(bc.status, "❓")
        b.button(
            text=f"{icon} {bc.title[:28]}",
            callback_data=AdminBroadcastCallback(action="view", broadcast_id=bc.id, page=page),
        )
    b.adjust(1)
    _add_pagination_broadcasts(b, page, total)
    b.row(
        InlineKeyboardButton(text="➕ Создать", callback_data=AdminBroadcastCallback(action="create", broadcast_id=0, page=1).pack()),
        _main_btn(),
    )
    return b.as_markup()


def get_broadcast_card_keyboard(broadcast) -> InlineKeyboardMarkup:
    from apps.broadcasts.models import BroadcastStatus
    b = InlineKeyboardBuilder()
    if broadcast.status == BroadcastStatus.DRAFT:
        b.button(text="✅ Подтвердить", callback_data=AdminBroadcastCallback(action="confirm", broadcast_id=broadcast.id, page=1))
    if broadcast.status in (BroadcastStatus.DRAFT, BroadcastStatus.CONFIRMED):
        b.button(text="🚀 Запустить", callback_data=AdminBroadcastCallback(action="launch_ask", broadcast_id=broadcast.id, page=1))
    b.button(text="📋 Логи доставки", callback_data=AdminBroadcastCallback(action="logs", broadcast_id=broadcast.id, page=1))
    b.button(text="🔙 К списку", callback_data=AdminBroadcastCallback(action="list", broadcast_id=0, page=1))
    b.adjust(1)
    return b.as_markup()


def get_broadcast_launch_confirm_keyboard(broadcast_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Да, запустить!", callback_data=AdminBroadcastCallback(action="launch", broadcast_id=broadcast_id, page=1))
    b.button(text="❌ Отмена", callback_data=AdminBroadcastCallback(action="view", broadcast_id=broadcast_id, page=1))
    b.adjust(2)
    return b.as_markup()


def get_audience_select_keyboard() -> InlineKeyboardMarkup:
    from apps.broadcasts.models import BroadcastAudience
    b = InlineKeyboardBuilder()
    options = [
        (BroadcastAudience.ALL, "📢 Всем пользователям"),
        (BroadcastAudience.ACTIVE, "✅ Только активным"),
        (BroadcastAudience.INVITED, "🔑 Активированным по ключу"),
    ]
    for val, label in options:
        b.button(
            text=label,
            callback_data=AdminBroadcastCallback(action=f"aud_{val}", broadcast_id=0, page=1),
        )
    b.button(text="❌ Отмена", callback_data=AdminMenuCallback(section="broadcasts"))
    b.adjust(1)
    return b.as_markup()


def get_broadcast_delivery_logs_keyboard(page: int, total: int, broadcast_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    _add_pagination_logs(b, page, total, broadcast_id)
    b.row(InlineKeyboardButton(
        text="🔙 К рассылке",
        callback_data=AdminBroadcastCallback(action="view", broadcast_id=broadcast_id, page=1).pack(),
    ))
    return b.as_markup()


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Обновить", callback_data=AdminStatsCallback(action="refresh"))
    b.button(text="🔙 Главное меню", callback_data=AdminMenuCallback(section="main"))
    b.adjust(1)
    return b.as_markup()


# ── Withdrawals ───────────────────────────────────────────────────────────────

_WD_STATUS_ICONS = {"pending": "⏳", "approved": "✅", "rejected": "❌"}


def get_withdrawals_list_keyboard(withdrawals, page: int, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for wd in withdrawals:
        icon = _WD_STATUS_ICONS.get(wd.status, "❓")
        b.button(
            text=f"{icon} #{wd.pk} {wd.user.display_name} — {wd.amount} руб.",
            callback_data=AdminWithdrawalCallback(action="view", withdrawal_id=wd.id, page=page),
        )
    b.adjust(1)
    # Pagination
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    row = []
    if page > 1:
        row.append(InlineKeyboardButton(text="◀️", callback_data=AdminWithdrawalCallback(action="list", page=page - 1).pack()))
    row.append(InlineKeyboardButton(text=f"· {page}/{total_pages} ·", callback_data=AdminWithdrawalCallback(action="noop").pack()))
    if page < total_pages:
        row.append(InlineKeyboardButton(text="▶️", callback_data=AdminWithdrawalCallback(action="list", page=page + 1).pack()))
    if row:
        b.row(*row)
    b.row(_main_btn())
    return b.as_markup()


def get_withdrawal_card_keyboard(withdrawal, back_page: int = 1) -> InlineKeyboardMarkup:
    from apps.withdrawals.models import WithdrawalStatus
    b = InlineKeyboardBuilder()
    if withdrawal.status == WithdrawalStatus.PENDING:
        b.button(text="✅ Исполнить", callback_data=AdminWithdrawalCallback(action="approve", withdrawal_id=withdrawal.id, page=back_page))
        b.button(text="❌ Отклонить", callback_data=AdminWithdrawalCallback(action="reject", withdrawal_id=withdrawal.id, page=back_page))
    b.button(text="🔙 К списку", callback_data=AdminWithdrawalCallback(action="list", page=back_page))
    b.adjust(2, 1) if withdrawal.status == "pending" else b.adjust(1)
    return b.as_markup()


def get_withdrawal_admin_notify_keyboard(withdrawal_id: int) -> InlineKeyboardMarkup:
    """Keyboard sent to admins in notification message."""
    b = InlineKeyboardBuilder()
    b.button(text="✅ Исполнить", callback_data=AdminWithdrawalCallback(action="approve", withdrawal_id=withdrawal_id).pack())
    b.button(text="❌ Отклонить", callback_data=AdminWithdrawalCallback(action="reject", withdrawal_id=withdrawal_id).pack())
    b.adjust(2)
    return b.as_markup()
