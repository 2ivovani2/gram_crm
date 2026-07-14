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
    b.button(text="⚠️ Мои штрафы", callback_data=CtrlWorkerCB(action="my_penalties"))
    b.adjust(2, 1, 1)
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


def worker_cancel_withdrawal() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data=CtrlWorkerCB(action="cancel"))
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
