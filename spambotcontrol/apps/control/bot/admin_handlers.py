"""
Admin-facing handlers for the Gramly Control bot.
Report review (with rejection comment FSM), penalty management, broadcasts.
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from apps.telegram_bot.permissions import IsAdmin
from apps.users.models import User
from apps.control.bot.keyboards import (
    CtrlAdminCB, admin_control_main_menu, admin_report_actions,
    admin_penalty_actions, admin_confirm_broadcast, admin_back,
)
from apps.control.bot.states import (
    AdminPenaltyCreateState, AdminBroadcastControlState, AdminReportReviewState,
)

router = Router(name="control_admin")


# ── Entry point ────────────────────────────────────────────────────────────────

@router.message(Command("control"), IsAdmin())
async def cmd_control(message: Message, db_user: User, state: FSMContext):
    await state.clear()
    await message.answer(
        "🛠 <b>Грамли Контроль — Панель администратора</b>\n\nВыберите раздел:",
        reply_markup=admin_control_main_menu(),
    )


@router.callback_query(CtrlAdminCB.filter(F.action == "main"), IsAdmin())
async def cb_admin_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(
        "🛠 <b>Грамли Контроль — Панель администратора</b>\n\nВыберите раздел:",
        reply_markup=admin_control_main_menu(),
    )


# ── Reports ────────────────────────────────────────────────────────────────────

async def _show_reports_list(callback: CallbackQuery, db_user: User, only_mine: bool = True):
    from asgiref.sync import sync_to_async
    from apps.control.services import ReportService
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    if only_mine:
        pending = await sync_to_async(
            lambda: list(ReportService.get_pending_for_admin(db_user)[:15])
        )()
        title = "📋 <b>Отчёты на вашей проверке</b>"
        empty_text = (
            "✅ Нет отчётов на вашей проверке.\n\n"
            "Отчёты попадают к вам, когда сотрудник сдаёт отчёт по вашему шаблону."
        )
    else:
        pending = await sync_to_async(
            lambda: list(ReportService.get_all_pending()[:15])
        )()
        title = "📋 <b>Все отчёты без шаблона / legacy</b>"
        empty_text = "✅ Нет отчётов, ожидающих проверки."

    await callback.answer()

    if not pending:
        await callback.message.edit_text(empty_text, reply_markup=admin_back())
        return

    text = f"{title} ({len(pending)}):\n\n"
    b = InlineKeyboardBuilder()
    for r in pending:
        username = f"@{r.user.telegram_username}" if r.user.telegram_username else str(r.user.telegram_id)
        tmpl_label = f" [{r.template.name}]" if r.template and r.template.name else ""
        status_icon = {"on_moderation": "🔵", "resubmitted": "🔄", "pending": "⏳"}.get(r.status, "📋")
        text += f"{status_icon} #{r.pk} {username}{tmpl_label} — {r.period_label}\n"
        b.button(
            text=f"#{r.pk} {username}",
            callback_data=CtrlAdminCB(action="rep_view", obj_id=r.pk),
        )

    b.button(text="◀️ Назад", callback_data=CtrlAdminCB(action="main"))
    b.adjust(1)
    await callback.message.edit_text(text, reply_markup=b.as_markup())


@router.callback_query(CtrlAdminCB.filter(F.action == "reports"), IsAdmin())
async def cb_reports_list(callback: CallbackQuery, db_user: User):
    await _show_reports_list(callback, db_user, only_mine=True)


@router.callback_query(CtrlAdminCB.filter(F.action == "reports_all"), IsAdmin())
async def cb_reports_list_all(callback: CallbackQuery, db_user: User):
    await _show_reports_list(callback, db_user, only_mine=False)


@router.callback_query(CtrlAdminCB.filter(F.action == "rep_view"), IsAdmin())
async def cb_report_view(callback: CallbackQuery, callback_data: CtrlAdminCB, db_user: User):
    from asgiref.sync import sync_to_async
    from apps.control.models import EmployeeReport

    report = await sync_to_async(
        lambda: EmployeeReport.objects.select_related("user", "template").filter(pk=callback_data.obj_id).first()
    )()
    if not report:
        await callback.answer("Отчёт не найден", show_alert=True)
        return

    username = f"@{report.user.telegram_username}" if report.user.telegram_username else str(report.user.telegram_id)
    tmpl_label = f"\nШаблон: {report.template.name}" if report.template and report.template.name else ""
    status_icon = {
        "on_moderation": "🔵", "resubmitted": "🔄", "pending": "⏳",
        "accepted": "✅", "rejected": "❌", "overdue": "🚨",
    }.get(report.status, "📋")

    text = (
        f"📋 <b>Отчёт #{report.pk}</b>\n\n"
        f"Сотрудник: {username}{tmpl_label}\n"
        f"Период: {report.period_label}\n"
        f"Подан: {report.submitted_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"Статус: {status_icon} {report.get_status_display()}\n"
    )
    if report.review_comment:
        text += f"Предыдущий комментарий: {report.review_comment}\n"
    if report.correction_deadline:
        text += f"Срок исправления: {report.correction_deadline.strftime('%d.%m %H:%M')}\n"
    if report.text_content:
        text += f"\n<b>Текст:</b>\n{report.text_content[:800]}"

    await callback.answer()
    if report.telegram_file_id and report.file_type == "document":
        await callback.message.answer_document(
            document=report.telegram_file_id,
            caption=text,
            parse_mode="HTML",
            reply_markup=admin_report_actions(report.pk),
        )
    elif report.telegram_file_id and report.file_type == "photo":
        await callback.message.answer_photo(
            photo=report.telegram_file_id,
            caption=text,
            parse_mode="HTML",
            reply_markup=admin_report_actions(report.pk),
        )
    else:
        await callback.message.edit_text(text, reply_markup=admin_report_actions(report.pk))


# ── Report: ACCEPT (immediate) ────────────────────────────────────────────────

@router.callback_query(CtrlAdminCB.filter(F.action == "rep_accept"), IsAdmin())
async def cb_rep_accept(callback: CallbackQuery, callback_data: CtrlAdminCB, db_user: User):
    from asgiref.sync import sync_to_async
    from apps.control.models import EmployeeReport
    from apps.control.services import ReportService

    report = await sync_to_async(
        lambda: EmployeeReport.objects.select_related("user", "template").filter(pk=callback_data.obj_id).first()
    )()
    if not report:
        await callback.answer("Отчёт не найден", show_alert=True)
        return

    await sync_to_async(ReportService.accept_report)(report, db_user)

    # Notify worker
    worker_msg = (
        f"✅ <b>Ваш отчёт принят!</b>\n\n"
        f"Отчёт за {report.period_label} принят администратором.\n"
        f"Вывод средств разблокирован."
    )
    try:
        await callback.bot.send_message(report.user.telegram_id, worker_msg, parse_mode="HTML")
    except Exception:
        pass

    await callback.answer("✅ Отчёт принят")
    await callback.message.edit_text(
        f"✅ Отчёт #{report.pk} принят.",
        reply_markup=admin_back(),
    )


# ── Report: REJECT — enter FSM to collect comment ────────────────────────────

@router.callback_query(CtrlAdminCB.filter(F.action == "rep_reject"), IsAdmin())
async def cb_rep_reject(callback: CallbackQuery, callback_data: CtrlAdminCB,
                         db_user: User, state: FSMContext):
    from asgiref.sync import sync_to_async
    from apps.control.models import EmployeeReport

    report = await sync_to_async(
        lambda: EmployeeReport.objects.select_related("user", "template").filter(pk=callback_data.obj_id).first()
    )()
    if not report:
        await callback.answer("Отчёт не найден", show_alert=True)
        return

    username = f"@{report.user.telegram_username}" if report.user.telegram_username else str(report.user.telegram_id)

    # Remember which report we're rejecting
    await state.set_state(AdminReportReviewState.waiting_for_comment)
    await state.update_data(report_id=report.pk, action="reject")

    # Build context about correction deadline
    hours = 24
    if report.template and report.template.correction_deadline_hours:
        hours = report.template.correction_deadline_hours

    await callback.answer()
    await callback.message.edit_text(
        f"❌ <b>Отклонение отчёта #{report.pk}</b>\n\n"
        f"Сотрудник: {username}\n"
        f"Период: {report.period_label}\n\n"
        f"Напишите причину отклонения и что нужно исправить.\n"
        f"Сотруднику будет дано <b>{hours} ч.</b> на повторную подачу.",
        reply_markup=admin_back(),
    )


@router.message(AdminReportReviewState.waiting_for_comment, IsAdmin())
async def process_reject_comment(message: Message, db_user: User, state: FSMContext):
    from asgiref.sync import sync_to_async
    from apps.control.models import EmployeeReport
    from apps.control.services import ReportService

    data = await state.get_data()
    report_id = data.get("report_id")
    comment = message.text or ""

    report = await sync_to_async(
        lambda: EmployeeReport.objects.select_related("user", "template").filter(pk=report_id).first()
    )()
    if not report:
        await state.clear()
        await message.answer("Отчёт не найден.", reply_markup=admin_back())
        return

    await sync_to_async(ReportService.reject_report)(report, db_user, comment)
    await state.clear()

    # Get template instructions for the notification
    hours = 24
    instructions = ""
    if report.template:
        hours = report.template.correction_deadline_hours or 24
        instructions = report.template.instructions

    worker_msg = (
        f"❌ <b>Ваш отчёт отклонён</b>\n\n"
        f"Период: {report.period_label}\n\n"
        f"<b>Причина:</b>\n{comment}\n\n"
    )
    if instructions:
        worker_msg += f"<b>Требования к отчёту:</b>\n{instructions}\n\n"
    worker_msg += (
        f"У вас есть <b>{hours} ч.</b> для повторной подачи.\n"
        f"Нажмите «📝 Подать отчёт» в меню."
    )

    try:
        await message.bot.send_message(
            report.user.telegram_id, worker_msg, parse_mode="HTML"
        )
    except Exception:
        pass

    await message.answer(
        f"❌ Отчёт #{report.pk} отклонён. Сотрудник уведомлён.",
        reply_markup=admin_back(),
    )


# ── Penalties ──────────────────────────────────────────────────────────────────

@router.callback_query(CtrlAdminCB.filter(F.action == "penalties"), IsAdmin())
async def cb_penalties_list(callback: CallbackQuery, db_user: User):
    from asgiref.sync import sync_to_async
    from apps.control.services import PenaltyService
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    penalties = await sync_to_async(lambda: list(PenaltyService.get_pending_penalties()[:15]))()
    await callback.answer()

    if not penalties:
        await callback.message.edit_text(
            "✅ Нет штрафов, требующих внимания.",
            reply_markup=admin_back(),
        )
        return

    text = f"⚠️ <b>Штрафы (активны / оспариваются)</b> ({len(penalties)}):\n\n"
    b = InlineKeyboardBuilder()
    for p in penalties:
        username = f"@{p.user.telegram_username}" if p.user.telegram_username else str(p.user.telegram_id)
        status_icon = "⏳" if p.status == "pending" else "🔄"
        text += f"{status_icon} #{p.pk} {username} — {p.amount:.2f} ₽ — {p.reason[:40]}\n"
        b.button(
            text=f"#{p.pk} {username}",
            callback_data=CtrlAdminCB(action="pen_view", obj_id=p.pk),
        )

    b.button(text="◀️ Назад", callback_data=CtrlAdminCB(action="main"))
    b.adjust(1)
    await callback.message.edit_text(text, reply_markup=b.as_markup())


@router.callback_query(CtrlAdminCB.filter(F.action == "pen_view"), IsAdmin())
async def cb_penalty_view(callback: CallbackQuery, callback_data: CtrlAdminCB, db_user: User):
    from asgiref.sync import sync_to_async
    from apps.control.models import Penalty

    penalty = await sync_to_async(
        lambda: Penalty.objects.select_related("user", "created_by", "report").filter(pk=callback_data.obj_id).first()
    )()
    if not penalty:
        await callback.answer("Штраф не найден", show_alert=True)
        return

    username = f"@{penalty.user.telegram_username}" if penalty.user.telegram_username else str(penalty.user.telegram_id)
    created_by = (
        f"@{penalty.created_by.telegram_username}" if penalty.created_by and penalty.created_by.telegram_username
        else (str(penalty.created_by.telegram_id) if penalty.created_by else "Авто")
    )
    text = (
        f"⚠️ <b>Штраф #{penalty.pk}</b>\n\n"
        f"Сотрудник: {username}\n"
        f"Тип: {penalty.get_type_display()}\n"
        f"Сумма: <b>{penalty.amount:.2f} ₽</b>\n"
        f"Причина: {penalty.reason}\n"
        f"Статус: {penalty.get_status_display()}\n"
        f"Назначил: {created_by}\n"
    )
    if penalty.report_id:
        text += f"Отчёт: #{penalty.report_id}\n"
    if penalty.comment:
        text += f"Комментарий: {penalty.comment}\n"
    if penalty.dispute_comment:
        text += f"\n💬 <b>Оспаривание:</b>\n{penalty.dispute_comment}"

    await callback.answer()
    await callback.message.edit_text(text, reply_markup=admin_penalty_actions(penalty.pk))


async def _resolve_penalty(callback: CallbackQuery, callback_data: CtrlAdminCB,
                           db_user: User, action: str):
    from asgiref.sync import sync_to_async
    from apps.control.models import Penalty

    penalty = await sync_to_async(
        lambda: Penalty.objects.select_related("user").filter(pk=callback_data.obj_id).first()
    )()
    if not penalty:
        await callback.answer("Штраф не найден", show_alert=True)
        return

    if action == "accept":
        await sync_to_async(penalty.accept)(db_user)
        result = "подтверждён ❌"
        worker_msg = f"❌ <b>Штраф подтверждён</b>\n\nШтраф #{penalty.pk} ({penalty.amount:.2f} ₽) подтверждён администратором."
    elif action == "reject":
        await sync_to_async(penalty.reject)(db_user)
        result = "отменён ✅"
        worker_msg = f"✅ <b>Штраф отменён</b>\n\nШтраф #{penalty.pk} ({penalty.amount:.2f} ₽) отменён администратором."
    else:
        await sync_to_async(penalty.delete_soft)(db_user)
        result = "удалён 🗑"
        worker_msg = f"🗑 <b>Штраф удалён</b>\n\nШтраф #{penalty.pk} ({penalty.amount:.2f} ₽) удалён администратором."

    try:
        await callback.bot.send_message(
            penalty.user.telegram_id, worker_msg, parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.answer(f"Штраф {result}")
    await callback.message.edit_text(
        f"Штраф #{penalty.pk} — {result}", reply_markup=admin_back()
    )


@router.callback_query(CtrlAdminCB.filter(F.action == "pen_accept"), IsAdmin())
async def cb_pen_accept(callback: CallbackQuery, callback_data: CtrlAdminCB, db_user: User):
    await _resolve_penalty(callback, callback_data, db_user, "accept")


@router.callback_query(CtrlAdminCB.filter(F.action == "pen_reject"), IsAdmin())
async def cb_pen_reject(callback: CallbackQuery, callback_data: CtrlAdminCB, db_user: User):
    await _resolve_penalty(callback, callback_data, db_user, "reject")


@router.callback_query(CtrlAdminCB.filter(F.action == "pen_delete"), IsAdmin())
async def cb_pen_delete(callback: CallbackQuery, callback_data: CtrlAdminCB, db_user: User):
    await _resolve_penalty(callback, callback_data, db_user, "delete")


# ── Create manual penalty ─────────────────────────────────────────────────────

@router.callback_query(CtrlAdminCB.filter(F.action == "create_penalty"), IsAdmin())
async def cb_create_penalty_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminPenaltyCreateState.waiting_for_username)
    await callback.answer()
    await callback.message.edit_text(
        "➕ <b>Создать штраф</b>\n\nВведите @username сотрудника:",
        reply_markup=admin_back(),
    )


@router.message(AdminPenaltyCreateState.waiting_for_username, IsAdmin())
async def penalty_got_username(message: Message, state: FSMContext):
    from asgiref.sync import sync_to_async
    from apps.users.models import User

    username = (message.text or "").lstrip("@").strip()
    worker = await sync_to_async(
        lambda: User.objects.filter(telegram_username__iexact=username).first()
    )()
    if not worker:
        await message.answer("❌ Пользователь не найден. Введите корректный @username:")
        return

    await state.update_data(worker_id=worker.pk, worker_name=f"@{worker.telegram_username}")
    await state.set_state(AdminPenaltyCreateState.waiting_for_amount)
    await message.answer(f"Сотрудник: @{worker.telegram_username}\n\nВведите сумму штрафа (числом, руб.):")


@router.message(AdminPenaltyCreateState.waiting_for_amount, IsAdmin())
async def penalty_got_amount(message: Message, state: FSMContext):
    from decimal import Decimal, InvalidOperation
    try:
        amount = Decimal(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError()
    except (InvalidOperation, ValueError):
        await message.answer("❌ Введите корректную сумму (например: 500):")
        return

    await state.update_data(amount=str(amount))
    await state.set_state(AdminPenaltyCreateState.waiting_for_reason)
    await message.answer("Введите причину штрафа:")


@router.message(AdminPenaltyCreateState.waiting_for_reason, IsAdmin())
async def penalty_got_reason(message: Message, db_user: User, state: FSMContext):
    from asgiref.sync import sync_to_async
    from decimal import Decimal
    from apps.control.services import PenaltyService
    from apps.users.models import User as U

    data = await state.get_data()
    worker = await sync_to_async(U.objects.get)(pk=data["worker_id"])
    amount = Decimal(data["amount"])
    reason = message.text or ""

    penalty = await sync_to_async(PenaltyService.create_manual)(
        admin=db_user, worker=worker, amount=amount, reason=reason
    )
    await state.clear()

    from apps.control.bot.keyboards import CtrlWorkerCB
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Оспорить", callback_data=CtrlWorkerCB(action=f"dispute_{penalty.pk}"))

    try:
        await message.bot.send_message(
            worker.telegram_id,
            f"⚠️ <b>Вам начислен штраф</b>\n\n"
            f"Сумма: <b>{amount:.2f} ₽</b>\n"
            f"Причина: {reason}\n\n"
            f"Вы можете оспорить штраф, нажав кнопку ниже.",
            parse_mode="HTML",
            reply_markup=b.as_markup(),
        )
    except Exception:
        pass

    await message.answer(
        f"✅ Штраф #{penalty.pk} создан:\n"
        f"Сотрудник: {data['worker_name']}\n"
        f"Сумма: {amount:.2f} ₽\n"
        f"Причина: {reason}",
        reply_markup=admin_back(),
    )


# ── Broadcast ─────────────────────────────────────────────────────────────────

@router.callback_query(CtrlAdminCB.filter(F.action == "broadcast"), IsAdmin())
async def cb_broadcast_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminBroadcastControlState.waiting_for_text)
    await callback.answer()
    await callback.message.edit_text(
        "📢 <b>Рассылка сотрудникам</b>\n\nВведите текст сообщения (поддерживается HTML):",
        reply_markup=admin_back(),
    )


@router.message(AdminBroadcastControlState.waiting_for_text, IsAdmin())
async def broadcast_got_text(message: Message, state: FSMContext):
    text = message.text or message.caption or ""
    if not text:
        await message.answer("❌ Введите текст сообщения:")
        return

    await state.update_data(text=text)
    await state.set_state(AdminBroadcastControlState.confirm)
    await message.answer(
        f"<b>Предпросмотр рассылки:</b>\n\n{text}\n\n"
        f"Отправить всем активным сотрудникам?",
        reply_markup=admin_confirm_broadcast(),
    )


@router.callback_query(CtrlAdminCB.filter(F.action == "bc_confirm"), IsAdmin())
async def cb_broadcast_confirm(callback: CallbackQuery, db_user: User, state: FSMContext):
    from asgiref.sync import sync_to_async
    from apps.users.models import UserRole, UserStatus

    data = await state.get_data()
    text = data.get("text", "")
    await state.clear()

    if not text:
        await callback.answer("Текст не найден", show_alert=True)
        return

    workers = await sync_to_async(
        lambda: list(
            User.objects.filter(
                role=UserRole.WORKER,
                status=UserStatus.ACTIVE,
                is_blocked_bot=False,
            ).values_list("telegram_id", flat=True)
        )
    )()

    await callback.answer("Рассылка запущена...")
    await callback.message.edit_text(f"📤 Отправляю рассылку {len(workers)} сотрудникам...")

    sent = 0
    failed = 0
    for tg_id in workers:
        try:
            await callback.bot.send_message(tg_id, text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1

    await callback.message.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\nОтправлено: {sent}\nОшибок: {failed}",
        reply_markup=admin_back(),
    )


@router.callback_query(CtrlAdminCB.filter(F.action == "bc_cancel"), IsAdmin())
async def cb_broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.edit_text(
        "🛠 <b>Грамли Контроль — Панель администратора</b>",
        reply_markup=admin_control_main_menu(),
    )
