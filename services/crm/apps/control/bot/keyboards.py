"""Keyboards for the Gramly Control bot."""
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData


# ── Callback data ─────────────────────────────────────────────────────────────

class CtrlWorkerCB(CallbackData, prefix="ctrl_w"):
    action: str


class CtrlAdminCB(CallbackData, prefix="ctrl_a"):
    action: str
    obj_id: int = 0


class CtrlAccountantCB(CallbackData, prefix="ctrl_acc"):
    action: str
    obj_id: int = 0


# ── Worker keyboards ──────────────────────────────────────────────────────────

def worker_main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 KPI", callback_data=CtrlWorkerCB(action="kpi"))
    b.button(text="💸 Вывод", callback_data=CtrlWorkerCB(action="withdraw"))
    b.button(text="📝 Подать отчёт", callback_data=CtrlWorkerCB(action="submit_report"))
    b.button(text="⏰ Дедлайны", callback_data=CtrlWorkerCB(action="dl.all.0"))
    b.button(text="⚠️ Мои штрафы", callback_data=CtrlWorkerCB(action="my_penalties"))
    b.button(text="💳 Мои адреса", callback_data=CtrlWorkerCB(action="my_addresses"))
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def worker_kpi_keyboard(has_doc: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if has_doc:
        b.button(text="📄 Скачать KPI-документ", callback_data=CtrlWorkerCB(action="kpi_doc"))
    b.button(text="◀️ Назад", callback_data=CtrlWorkerCB(action="main_menu"))
    b.adjust(1)
    return b.as_markup()


def worker_back_to_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Главное меню", callback_data=CtrlWorkerCB(action="main_menu"))
    return b.as_markup()


def worker_cancel_report() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data=CtrlWorkerCB(action="cancel"))
    return b.as_markup()


def worker_report_upload_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Завершить отчёт", callback_data=CtrlWorkerCB(action="finish_report"))
    b.button(text="➕ Отправить ещё файл", callback_data=CtrlWorkerCB(action="report_more"))
    b.adjust(1)
    return b.as_markup()


def worker_cancel_withdrawal() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data=CtrlWorkerCB(action="cancel"))
    return b.as_markup()


def worker_address_select_keyboard(addresses: list) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for addr in addresses:
        b.button(
            text=f"💳 {addr.name}",
            callback_data=CtrlWorkerCB(action=f"addr_{addr.pk}"),
        )
    b.button(text="✏️ Ввести адрес вручную", callback_data=CtrlWorkerCB(action="addr_new"))
    b.button(text="❌ Отмена", callback_data=CtrlWorkerCB(action="cancel"))
    b.adjust(1)
    return b.as_markup()


def worker_addresses_list_keyboard(addresses: list) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for addr in addresses:
        b.button(
            text=f"🗑 {addr.name} ({addr.address[:12]}…)",
            callback_data=CtrlWorkerCB(action=f"del_addr_{addr.pk}"),
        )
    b.button(text="➕ Добавить адрес", callback_data=CtrlWorkerCB(action="add_addr"))
    b.button(text="◀️ Назад", callback_data=CtrlWorkerCB(action="main_menu"))
    b.adjust(1)
    return b.as_markup()


def penalty_dispute_keyboard(penalty_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Оспорить", callback_data=CtrlWorkerCB(action=f"dispute_{penalty_id}"))
    b.button(text="◀️ Назад", callback_data=CtrlWorkerCB(action="my_penalties"))
    b.adjust(1)
    return b.as_markup()


def worker_template_select_keyboard(templates: list) -> InlineKeyboardMarkup:
    """Show list of report templates for worker to pick from."""
    b = InlineKeyboardBuilder()
    for tmpl in templates:
        label = tmpl.name or f"Шаблон #{tmpl.pk}"
        b.button(
            text=f"📋 {label}",
            callback_data=CtrlWorkerCB(action=f"pick_tmpl_{tmpl.pk}"),
        )
    b.button(text="❌ Отмена", callback_data=CtrlWorkerCB(action="cancel"))
    b.adjust(1)
    return b.as_markup()


def worker_report_decision_keyboard(report_id: int, can_edit: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if can_edit:
        b.button(
            text="✏️ Редактировать",
            callback_data=CtrlWorkerCB(action=f"edit_report_{report_id}"),
        )
    b.button(text="Закрыть", callback_data=CtrlWorkerCB(action="close_notice"))
    b.adjust(2 if can_edit else 1)
    return b.as_markup()


def worker_deadline_list_keyboard(items, selected_filter: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    filters = [
        ("Сегодня", "today"), ("Завтра", "tomorrow"), ("7 дней", "week"),
        ("Просрочено", "overdue"), ("Все", "all"),
    ]
    for label, value in filters:
        marker = "✓ " if selected_filter == value else ""
        b.button(text=f"{marker}{label}", callback_data=CtrlWorkerCB(action=f"dl.{value}.0"))
    b.adjust(2, 3)
    for item in items:
        b.button(
            text=f"{item.status_label.split()[0]} {item.deadline_at:%d.%m %H:%M} · {item.title}",
            callback_data=CtrlWorkerCB(action=f"dli.{item.template_id}.{item.report_date:%Y%m%d}.{selected_filter}.{page}"),
        )
    if total_pages > 1:
        if page > 0:
            b.button(text="◀️", callback_data=CtrlWorkerCB(action=f"dl.{selected_filter}.{page - 1}"))
        b.button(text=f"{page + 1}/{total_pages}", callback_data=CtrlWorkerCB(action="noop"))
        if page + 1 < total_pages:
            b.button(text="▶️", callback_data=CtrlWorkerCB(action=f"dl.{selected_filter}.{page + 1}"))
    b.button(text="◀️ Главное меню", callback_data=CtrlWorkerCB(action="main_menu"))
    b.adjust(1)
    return b.as_markup()


def worker_deadline_detail_keyboard(item, selected_filter: str, page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if item.can_edit:
        if item.report_id:
            action = f"edit_report_{item.report_id}"
        else:
            action = f"dle.{item.template_id}.{item.report_date:%Y%m%d}"
        b.button(text="✏️ Редактировать", callback_data=CtrlWorkerCB(action=action))
    if item.can_open and item.report_id:
        b.button(text="📋 Открыть отчёт", callback_data=CtrlWorkerCB(action=f"dlo.{item.report_id}"))
    b.button(text="◀️ К дедлайнам", callback_data=CtrlWorkerCB(action=f"dl.{selected_filter}.{page}"))
    b.adjust(1)
    return b.as_markup()


# ── Admin keyboards ───────────────────────────────────────────────────────────

def admin_control_main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📋 Мои отчёты на проверке", callback_data=CtrlAdminCB(action="reports"))
    b.button(text="📋 Все отчёты (без шаблона)", callback_data=CtrlAdminCB(action="reports_all"))
    b.button(text="⚠️ Штрафы", callback_data=CtrlAdminCB(action="penalties"))
    b.button(text="📢 Рассылка", callback_data=CtrlAdminCB(action="broadcast"))
    b.button(text="➕ Создать штраф", callback_data=CtrlAdminCB(action="create_penalty"))
    b.button(text="💸 Вывод средств", callback_data=CtrlAdminCB(action="withdraw"))
    b.adjust(1)
    return b.as_markup()


def admin_cancel_withdrawal() -> InlineKeyboardMarkup:
    from apps.telegram_bot.callbacks import AdminMenuCallback
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data=AdminMenuCallback(section="main"))
    return b.as_markup()


def admin_address_select_keyboard(addresses: list) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for addr in addresses:
        b.button(text=f"💳 {addr.name}", callback_data=CtrlAdminCB(action="addr_sel", obj_id=addr.pk))
    b.button(text="✏️ Ввести адрес вручную", callback_data=CtrlAdminCB(action="addr_new"))
    b.button(text="❌ Отмена", callback_data=CtrlAdminCB(action="main"))
    b.adjust(1)
    return b.as_markup()


def admin_addresses_list_keyboard(addresses: list) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for addr in addresses:
        b.button(
            text=f"🗑 {addr.name} ({addr.address[:12]}…)",
            callback_data=CtrlAdminCB(action="del_addr", obj_id=addr.pk),
        )
    b.button(text="➕ Добавить адрес", callback_data=CtrlAdminCB(action="add_addr"))
    b.button(text="◀️ Главное меню", callback_data=CtrlAdminCB(action="main"))
    b.adjust(1)
    return b.as_markup()


def admin_report_actions(report_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Принять", callback_data=CtrlAdminCB(action="rep_accept", obj_id=report_id))
    b.button(text="❌ Отклонить", callback_data=CtrlAdminCB(action="rep_reject", obj_id=report_id))
    b.button(text="◀️ Список отчётов", callback_data=CtrlAdminCB(action="reports"))
    b.adjust(2, 1)
    return b.as_markup()


def admin_penalty_actions(penalty_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить", callback_data=CtrlAdminCB(action="pen_accept", obj_id=penalty_id))
    b.button(text="❌ Отменить", callback_data=CtrlAdminCB(action="pen_reject", obj_id=penalty_id))
    b.button(text="🗑 Удалить", callback_data=CtrlAdminCB(action="pen_delete", obj_id=penalty_id))
    b.button(text="◀️ Список штрафов", callback_data=CtrlAdminCB(action="penalties"))
    b.adjust(3, 1)
    return b.as_markup()


def admin_confirm_broadcast() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Отправить всем", callback_data=CtrlAdminCB(action="bc_confirm"))
    b.button(text="❌ Отмена", callback_data=CtrlAdminCB(action="bc_cancel"))
    b.adjust(2)
    return b.as_markup()


def admin_back() -> InlineKeyboardMarkup:
    from apps.telegram_bot.callbacks import AdminMenuCallback
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Главное меню", callback_data=AdminMenuCallback(section="main"))
    return b.as_markup()


# ── Accountant keyboards ──────────────────────────────────────────────────────

def accountant_main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💳 Заявки на вывод", callback_data=CtrlAccountantCB(action="withdrawals"))
    b.adjust(1)
    return b.as_markup()


def accountant_withdrawal_actions(withdrawal_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔄 В обработку", callback_data=CtrlAccountantCB(action="w_processing", obj_id=withdrawal_id))
    b.button(text="📤 Чек отправлен", callback_data=CtrlAccountantCB(action="w_receipt", obj_id=withdrawal_id))
    b.button(text="✅ Выполнено", callback_data=CtrlAccountantCB(action="w_done", obj_id=withdrawal_id))
    b.button(text="❌ Отклонить", callback_data=CtrlAccountantCB(action="w_reject", obj_id=withdrawal_id))
    b.button(text="◀️ Список", callback_data=CtrlAccountantCB(action="withdrawals"))
    b.adjust(2, 2, 1)
    return b.as_markup()
