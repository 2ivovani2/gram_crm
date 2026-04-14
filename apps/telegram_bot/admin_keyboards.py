"""Admin-facing inline keyboards."""
from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from .callbacks import (
    AdminMenuCallback, AdminUserCallback, AdminInviteCallback,
    AdminBroadcastCallback, AdminStatsCallback, AdminSettingsCallback,
    AdminWithdrawalCallback, AdminDailyCallback, AdminApplicationCallback,
    AdminClientCallback,
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

def get_admin_main_menu(pending_requests: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    app_label = "📋 Заявки"
    if pending_requests > 0:
        app_label = f"📋 Заявки ({pending_requests})"
    b.button(text="👥 Пользователи", callback_data=AdminMenuCallback(section="users"))
    b.button(text=app_label, callback_data=AdminMenuCallback(section="applications"))
    b.button(text="📢 Рассылки", callback_data=AdminMenuCallback(section="broadcasts"))
    b.button(text="📊 Статистика", callback_data=AdminMenuCallback(section="stats"))
    b.button(text="🔗 Клиенты и ссылки", callback_data=AdminMenuCallback(section="clients"))
    b.button(text="💸 Выводы", callback_data=AdminMenuCallback(section="withdrawals"))
    b.button(text="📋 Ввод данных", callback_data=AdminMenuCallback(section="daily"))
    b.button(text="⚙️ Настройки", callback_data=AdminMenuCallback(section="settings"))
    b.adjust(2, 2, 2, 2)
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
    from apps.users.models import UserRole
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Изменить статус", callback_data=AdminUserCallback(action="change_status", user_id=user.id, page=back_page))
    b.button(text="🔗 Рабочая ссылка", callback_data=AdminSettingsCallback(action="set_work_url", user_id=user.id))
    b.button(text="👤 Привлечено людей", callback_data=AdminSettingsCallback(action="set_attracted", user_id=user.id))
    b.button(text="💰 Личная ставка", callback_data=AdminSettingsCallback(action="set_personal_rate", user_id=user.id))
    b.button(text="🤝 Ставка за рефералов", callback_data=AdminSettingsCallback(action="set_referral_rate", user_id=user.id))
    # Role assignment: show curator button for workers, worker button for curators
    if user.role == UserRole.WORKER:
        b.button(text="🎓 Назначить куратором", callback_data=AdminUserCallback(action="set_curator", user_id=user.id, page=back_page))
    elif user.role == UserRole.CURATOR:
        b.button(text="👷 Назначить воркером", callback_data=AdminUserCallback(action="set_worker", user_id=user.id, page=back_page))
    b.button(text="🔙 К списку", callback_data=AdminUserCallback(action="list", user_id=0, page=back_page))
    b.adjust(1)
    return b.as_markup()


def get_settings_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⚙️ Доли ставок (RateConfig)", callback_data=AdminSettingsCallback(action="set_rate_config"))
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


# ── Curator invite keyboards (reuse admin invite shapes) ──────────────────────

def get_curator_invites_list_keyboard(keys, page: int, total: int) -> InlineKeyboardMarkup:
    from .callbacks import CuratorCallback
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
        InlineKeyboardButton(text="🔙 Назад", callback_data=CuratorCallback(action="back_to_main").pack()),
    )
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

def get_stats_keyboard(period: str = "week") -> InlineKeyboardMarkup:
    """Period selector + refresh + back to main menu."""
    b = InlineKeyboardBuilder()
    periods = [
        ("today",     "📅 Сегодня"),
        ("week",      "📊 Эта неделя"),
        ("last_week", "📉 Прошлая неделя"),
        ("month",     "📆 Месяц"),
    ]
    row = []
    for p_val, p_label in periods:
        marker = "▶ " if p_val == period else ""
        row.append(InlineKeyboardButton(
            text=f"{marker}{p_label}",
            callback_data=AdminStatsCallback(action="period", period=p_val).pack(),
        ))
    b.row(*row[:2])
    b.row(*row[2:])
    b.row(
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=AdminStatsCallback(action="refresh", period=period).pack(),
        ),
        _main_btn(),
    )
    return b.as_markup()


# ── Daily report: entry menu + date picker ─────────────────────────────────────

def get_daily_entry_menu_keyboard(has_other_dates: bool = False) -> InlineKeyboardMarkup:
    """Entry menu: today + optionally date picker + back."""
    b = InlineKeyboardBuilder()
    b.button(text="📋 Внести за сегодня", callback_data=AdminDailyCallback(action="start"))
    if has_other_dates:
        b.button(text="📅 Внести за другой день", callback_data=AdminDailyCallback(action="pick_date"))
    b.button(text="🔙 Главное меню", callback_data=AdminMenuCallback(section="main"))
    b.adjust(1)
    return b.as_markup()


def get_daily_date_picker_keyboard(dates: list, missed_dates: set) -> InlineKeyboardMarkup:
    """Show list of recent dates without a DailyReport."""
    RU_MONTHS = ["янв", "фев", "мар", "апр", "май", "июн",
                 "июл", "авг", "сен", "окт", "ноя", "дек"]
    b = InlineKeyboardBuilder()
    for d in dates:
        label = f"{d.day} {RU_MONTHS[d.month - 1]}"
        if d in missed_dates:
            label = f"🔴 {label} (пропущен)"
        else:
            label = f"📅 {label}"
        b.button(
            text=label,
            callback_data=AdminDailyCallback(action="select_date", date_str=d.isoformat()),
        )
    b.adjust(1)
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data=AdminMenuCallback(section="daily").pack()))
    return b.as_markup()


# ── Daily report ──────────────────────────────────────────────────────────────

def get_daily_report_confirm_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить", callback_data=AdminDailyCallback(action="confirm"))
    b.button(text="❌ Отмена", callback_data=AdminMenuCallback(section="main"))
    b.adjust(2)
    return b.as_markup()


def get_rate_config_cancel_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data=AdminMenuCallback(section="settings"))
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


# ── Clients & Links ───────────────────────────────────────────────────────────

def get_clients_list_keyboard(clients, page: int, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for client in clients:
        b.button(
            text=f"👤 {client.nick} ({client.rate}$)",
            callback_data=AdminClientCallback(action="view_client", client_id=client.id, page=page),
        )
    b.adjust(1)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=AdminClientCallback(action="list", page=page - 1).pack()))
    nav.append(InlineKeyboardButton(text=f"· {page}/{total_pages} ·", callback_data=AdminClientCallback(action="noop").pack()))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=AdminClientCallback(action="list", page=page + 1).pack()))
    if nav:
        b.row(*nav)
    b.row(_main_btn())
    return b.as_markup()


def get_client_card_keyboard(client) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for link in client.links.all():
        status_icon = "🟢" if link.status == "active" else "🔴"
        b.button(
            text=f"{status_icon} {link.url[:45]}",
            callback_data=AdminClientCallback(action="view_link", client_id=client.id, link_id=link.id),
        )
    b.adjust(1)
    b.row(InlineKeyboardButton(text="🔙 К списку", callback_data=AdminClientCallback(action="list").pack()))
    return b.as_markup()


def get_link_card_keyboard(link) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if link.status == "active":
        b.button(
            text="🔴 Деактивировать ссылку",
            callback_data=AdminClientCallback(action="deactivate", link_id=link.id, client_id=link.client_id),
        )
        b.button(
            text="👷 Назначить воркера вручную",
            callback_data=AdminClientCallback(action="assign_ask", link_id=link.id, client_id=link.client_id),
        )
        b.adjust(1)
    b.row(InlineKeyboardButton(
        text="🔙 К клиенту",
        callback_data=AdminClientCallback(action="view_client", client_id=link.client_id).pack(),
    ))
    return b.as_markup()


def get_assign_workers_keyboard(workers, link_id: int, client_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for worker in workers:
        assignments_count = getattr(worker, "active_assignments", 0)
        b.button(
            text=f"👷 {worker.display_name} ({assignments_count} ссылок)",
            callback_data=AdminClientCallback(action="assign", link_id=link_id, client_id=client_id, worker_id=worker.id),
        )
    b.adjust(1)
    b.row(InlineKeyboardButton(
        text="🔙 Назад",
        callback_data=AdminClientCallback(action="view_link", link_id=link_id, client_id=client_id).pack(),
    ))
    return b.as_markup()
