"""
Worker-facing handlers for the Gramly Control bot.
Handles: personal cabinet, KPI, withdrawal, report submission, penalties.
"""
import datetime as dt
import html

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apps.users.models import User
from apps.control.bot.keyboards import (
    CtrlWorkerCB, worker_main_menu, worker_kpi_keyboard,
    worker_back_to_menu, worker_cancel_report, worker_cancel_withdrawal,
    worker_template_select_keyboard,
    worker_address_select_keyboard, worker_addresses_list_keyboard,
    worker_report_upload_keyboard,
    worker_deadline_list_keyboard, worker_deadline_detail_keyboard,
)
from apps.control.bot.states import SubmitReportState, WithdrawalState, DisputePenaltyState, CryptoAddressState

router = Router(name="control_worker")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _cabinet_text(db_user: User, balance) -> str:
    username = f"@{db_user.telegram_username}" if db_user.telegram_username else str(db_user.telegram_id)
    return (
        f"👤 <b>Личный кабинет</b>\n\n"
        f"Сотрудник: {username}\n\n"
        f"💰 Баланс: <b>{balance:.2f} ₽</b>"
    )


_DEADLINES_PAGE_SIZE = 6


def _deadline_detail_text(item) -> str:
    text = (
        f"📋 <b>{html.escape(item.title)}</b>\n\n"
        f"Дата отчёта: <b>{item.report_date:%d.%m.%Y}</b>\n"
        f"Дедлайн: <b>{item.deadline_at:%d.%m.%Y %H:%M} МСК</b>\n"
        f"Статус: {item.status_label}\n"
    )
    if item.editing_ends_at:
        text += f"\n🕐 Окно редактирования: до <b>{item.editing_ends_at:%d.%m.%Y %H:%M} МСК</b>\n"
    else:
        text += f"\nРедактирование: <b>{'Доступно' if item.can_edit else 'Недоступно'}</b>\n"
    if item.charged_penalty:
        text += f"\n⚠️ Уже начислено: <b>{item.charged_penalty:.2f} ₽</b>"
    if item.potential_penalty:
        text += f"\n⚠️ Потенциальный дополнительный штраф: <b>+{item.potential_penalty:.2f} ₽</b>"
    return text


async def _show_deadlines(callback: CallbackQuery, db_user: User, selected_filter: str, page: int) -> None:
    from asgiref.sync import sync_to_async
    from apps.control.deadlines import DeadlineFilter, ReportDeadlineProvider

    try:
        selected = DeadlineFilter(selected_filter)
    except ValueError:
        selected = DeadlineFilter.ALL
    items = await sync_to_async(ReportDeadlineProvider.list_for_user)(db_user, selected)
    total_pages = max(1, (len(items) + _DEADLINES_PAGE_SIZE - 1) // _DEADLINES_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    visible = items[page * _DEADLINES_PAGE_SIZE:(page + 1) * _DEADLINES_PAGE_SIZE]
    labels = {
        "today": "Сегодня", "tomorrow": "Завтра", "week": "Ближайшие 7 дней",
        "overdue": "Просроченные", "all": "Все актуальные",
    }
    lines = ["⏰ <b>ДЕДЛАЙНЫ</b>", f"\nФильтр: <b>{labels[selected.value]}</b>"]
    if visible:
        lines.append("")
        from apps.control.deadlines import DeadlineState, MSK
        from django.utils import timezone
        today = timezone.now().astimezone(MSK).date()
        current_group = None
        for item in visible:
            deadline_date = item.deadline_at.date()
            if item.state == DeadlineState.OVERDUE:
                group = "🔴 ПРОСРОЧЕНО"
            elif item.state == DeadlineState.MODERATION and deadline_date < today:
                group = "🟡 НА МОДЕРАЦИИ"
            elif deadline_date == today:
                group = "🟠 СЕГОДНЯ"
            elif deadline_date == today + dt.timedelta(days=1):
                group = "🟡 ЗАВТРА"
            else:
                group = "🟢 БЛИЖАЙШИЕ 7 ДНЕЙ"
            if group != current_group:
                lines.append(f"<b>{group}</b>")
                current_group = group
            lines.append(
                f"{item.status_label.split()[0]} <b>{item.deadline_at:%d.%m %H:%M}</b> — "
                f"{html.escape(item.title)}"
            )
    else:
        lines.append("\nЗдесь пока нет актуальных обязательств.")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=worker_deadline_list_keyboard(visible, selected.value, page, total_pages),
    )


async def _send_report_snapshot(message: Message, report) -> None:
    from asgiref.sync import sync_to_async
    from django.core.files.storage import default_storage
    from apps.control.models import ReportMediaStatus

    text = report.text_content.strip() or "Текстовая часть отсутствует."
    if len(text) > 3000:
        text = f"{text[:3000]}\n\n…текст сокращён для Telegram"
    await message.answer(
        f"📋 <b>Отчёт #{report.pk}</b> · версия {report.current_revision}\n\n{html.escape(text)}"
    )
    media = await sync_to_async(
        lambda: list(report.media_files.filter(
            revision=report.current_revision,
            status=ReportMediaStatus.READY,
        ).order_by("position", "id"))
    )()
    for item in media:
        source = item.telegram_file_id
        if not source and item.storage_key:
            source = await sync_to_async(default_storage.url)(item.storage_key)
        if not source:
            continue
        sender = {
            "photo": message.answer_photo,
            "video": message.answer_video,
            "animation": message.answer_animation,
            "sticker": message.answer_sticker,
            "video_note": message.answer_video_note,
            "document": message.answer_document,
        }.get(item.media_type, message.answer_document)
        await sender(source)


# ── Main menu (personal cabinet) ───────────────────────────────────────────────

async def send_worker_cabinet(event: Message | CallbackQuery, db_user: User, state: FSMContext):
    await state.clear()
    from asgiref.sync import sync_to_async
    from apps.control.services import ControlBalanceService

    balance = await sync_to_async(ControlBalanceService.get_available_balance)(db_user)
    text = _cabinet_text(db_user, balance)

    if isinstance(event, Message):
        await event.answer(text, reply_markup=worker_main_menu())
    else:
        await event.message.edit_text(text, reply_markup=worker_main_menu())


@router.callback_query(CtrlWorkerCB.filter(F.action == "main_menu"))
async def cb_main_menu(callback: CallbackQuery, db_user: User, state: FSMContext):
    await callback.answer()
    await send_worker_cabinet(callback, db_user, state)


@router.callback_query(CtrlWorkerCB.filter(F.action == "cancel"))
async def cb_cancel(callback: CallbackQuery, db_user: User, state: FSMContext):
    data = await state.get_data()
    report_id = data.get("active_report_id")
    await callback.answer("Отчёт уже сохранён" if report_id else "Отменено")
    await state.clear()
    if report_id:
        await callback.message.edit_text(
            f"✅ <b>Отчёт #{report_id} отправлен на проверку</b>",
            reply_markup=worker_back_to_menu(),
        )
        return
    await send_worker_cabinet(callback, db_user, state)


# ── KPI ────────────────────────────────────────────────────────────────────────

@router.callback_query(CtrlWorkerCB.filter(F.action == "kpi"))
async def cb_kpi(callback: CallbackQuery, db_user: User):
    from asgiref.sync import sync_to_async
    from apps.control.services import KPIService

    settings = await sync_to_async(KPIService.get_or_create_settings)(db_user)
    doc = await sync_to_async(KPIService.get_document)(db_user)

    text = (
        f"📊 <b>Ваши KPI-показатели</b>\n\n"
        f"💵 Базовая ставка: <b>{settings.base_rate:.2f} ₽</b>\n"
        f"🎁 Ставка премирования: <b>{settings.bonus_rate:.2f} ₽</b>\n"
        f"⚠️ Ставка штрафов: <b>{settings.penalty_rate:.2f} ₽</b>\n"
    )
    if settings.other_info:
        text += f"\n📌 Прочее:\n{settings.other_info}"

    await callback.answer()
    await callback.message.edit_text(
        text,
        reply_markup=worker_kpi_keyboard(has_doc=doc is not None),
    )


@router.callback_query(CtrlWorkerCB.filter(F.action == "kpi_doc"))
async def cb_kpi_doc(callback: CallbackQuery, db_user: User):
    from asgiref.sync import sync_to_async
    from apps.control.services import KPIService

    doc = await sync_to_async(KPIService.get_document)(db_user)
    if not doc:
        await callback.answer("Документ не найден", show_alert=True)
        return

    await callback.answer()
    try:
        file_url = doc.file.url
        await callback.message.answer_document(
            document=file_url,
            caption=f"📄 KPI-документ: {doc.original_filename or 'kpi.docx'}",
        )
    except Exception as e:
        await callback.answer(f"Ошибка при отправке файла: {e}", show_alert=True)


# ── Withdrawal ─────────────────────────────────────────────────────────────────

@router.callback_query(CtrlWorkerCB.filter(F.action == "withdraw"))
async def cb_withdraw(callback: CallbackQuery, db_user: User, state: FSMContext):
    from asgiref.sync import sync_to_async
    from apps.control.services import ReportService, ControlBalanceService

    blocked = await sync_to_async(ReportService.has_blocking_report)(db_user)
    if blocked:
        await callback.answer("❌ Вывод заблокирован: ваш отчёт ожидает проверки.", show_alert=True)
        return

    available = await sync_to_async(ControlBalanceService.get_available_balance)(db_user)
    if available <= 0:
        await callback.answer("❌ Недостаточно средств для вывода.", show_alert=True)
        return

    await state.set_state(WithdrawalState.waiting_for_amount)
    await callback.answer()
    await callback.message.edit_text(
        f"💸 <b>Вывод средств</b>\n\n"
        f"Доступно: <b>{available:.2f} ₽</b>\n\n"
        f"Введите сумму для вывода (₽):",
        reply_markup=worker_cancel_withdrawal(),
    )


@router.message(WithdrawalState.waiting_for_amount)
async def process_amount_input(message: Message, db_user: User, state: FSMContext):
    from decimal import Decimal, InvalidOperation
    from asgiref.sync import sync_to_async
    from apps.control.services import ControlBalanceService, ControlWithdrawalService
    from apps.control.models import ControlSettings

    text = (message.text or "").strip().replace(",", ".")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        await message.answer("❌ Введите корректную сумму числом:", reply_markup=worker_cancel_withdrawal())
        return

    available = await sync_to_async(ControlBalanceService.get_available_balance)(db_user)
    settings = await sync_to_async(ControlSettings.get)()

    if amount <= 0:
        await message.answer("❌ Сумма должна быть больше 0:", reply_markup=worker_cancel_withdrawal())
        return
    if amount > available:
        await message.answer(
            f"❌ Сумма превышает доступный баланс ({available:.2f} ₽).\nВведите корректную сумму:",
            reply_markup=worker_cancel_withdrawal(),
        )
        return
    if amount < settings.min_withdrawal_amount:
        await message.answer(
            f"❌ Минимальная сумма вывода — {settings.min_withdrawal_amount:.0f} ₽.\nВведите корректную сумму:",
            reply_markup=worker_cancel_withdrawal(),
        )
        return

    await state.update_data(amount=str(amount))

    addresses = await sync_to_async(ControlWithdrawalService.get_saved_addresses)(db_user)
    if addresses:
        await message.answer(
            f"💳 <b>Выберите адрес</b>\n\nСумма: <b>{amount:.2f} ₽</b>\n\nВыберите сохранённый адрес или введите новый:",
            reply_markup=worker_address_select_keyboard(addresses),
        )
    else:
        await state.set_state(WithdrawalState.waiting_for_wallet)
        await message.answer(
            f"💳 <b>Адрес кошелька</b>\n\nСумма: <b>{amount:.2f} ₽</b>\n\nВведите адрес USDT TRC20-кошелька:",
            reply_markup=worker_cancel_withdrawal(),
        )


@router.message(WithdrawalState.waiting_for_wallet)
async def process_wallet_input(message: Message, db_user: User, state: FSMContext):
    from decimal import Decimal

    wallet = (message.text or "").strip()
    if not wallet or len(wallet) < 20:
        await message.answer(
            "❌ Некорректный адрес кошелька. Введите корректный USDT TRC20-адрес:",
            reply_markup=worker_cancel_withdrawal(),
        )
        return

    data = await state.get_data()
    amount_str = data.get("amount")
    amount = Decimal(amount_str) if amount_str else None
    await _create_and_notify_withdrawal(message, db_user, wallet, amount, state)


async def _create_and_notify_withdrawal(message: Message, db_user: User, wallet: str, amount, state: FSMContext):
    from asgiref.sync import sync_to_async
    from apps.control.services import ControlWithdrawalService

    try:
        withdrawal = await sync_to_async(ControlWithdrawalService.create)(db_user, wallet, amount)
    except ValueError as e:
        await message.answer(f"❌ {e}", reply_markup=worker_back_to_menu())
        await state.clear()
        return

    await state.clear()

    processor_ids = await sync_to_async(ControlWithdrawalService.get_processor_ids)()

    username = f"@{db_user.telegram_username}" if db_user.telegram_username else str(db_user.telegram_id)
    notify_text = (
        f"💳 <b>Новая заявка на вывод</b>\n\n"
        f"Сотрудник: {username}\n"
        f"Сумма: <b>{withdrawal.amount:.2f} ₽</b>\n"
        f"Кошелёк USDT TRC20:\n<code>{wallet}</code>\n\n"
        f"ID заявки: #{withdrawal.pk}"
    )
    from apps.control.bot.keyboards import accountant_withdrawal_actions
    from apps.withdrawals.models import WithdrawalRequest
    notifications = []
    for tg_id in processor_ids:
        try:
            msg = await message.bot.send_message(
                tg_id, notify_text, parse_mode="HTML",
                reply_markup=accountant_withdrawal_actions(withdrawal.pk),
            )
            notifications.append({"telegram_id": tg_id, "message_id": msg.message_id})
        except Exception:
            pass

    if notifications:
        await sync_to_async(
            lambda: WithdrawalRequest.objects.filter(pk=withdrawal.pk).update(admin_notifications=notifications)
        )()

    await message.answer(
        f"✅ <b>Заявка на вывод создана</b>\n\n"
        f"Сумма: <b>{withdrawal.amount:.2f} ₽</b>\n"
        f"Кошелёк: <code>{wallet}</code>\n\n"
        f"Бухгалтер обработает заявку в ближайшее время.",
        reply_markup=worker_back_to_menu(),
    )


# ── Crypto addresses ───────────────────────────────────────────────────────────

@router.callback_query(CtrlWorkerCB.filter(F.action == "my_addresses"))
async def cb_my_addresses(callback: CallbackQuery, db_user: User, state: FSMContext):
    from asgiref.sync import sync_to_async
    from apps.control.services import ControlWithdrawalService

    addresses = await sync_to_async(ControlWithdrawalService.get_saved_addresses)(db_user)
    await state.clear()
    await callback.answer()
    if addresses:
        text = "💳 <b>Мои адреса</b>\n\nНажмите на адрес чтобы удалить его:"
    else:
        text = "💳 <b>Мои адреса</b>\n\nУ вас нет сохранённых адресов."
    await callback.message.edit_text(text, reply_markup=worker_addresses_list_keyboard(addresses))


@router.message(CryptoAddressState.waiting_for_name)
async def process_addr_name(message: Message, db_user: User, state: FSMContext):
    name = (message.text or "").strip()
    if not name or len(name) > 100:
        await message.answer(
            "❌ Введите название (до 100 символов):",
            reply_markup=worker_cancel_withdrawal(),
        )
        return
    await state.update_data(addr_name=name)
    await state.set_state(CryptoAddressState.waiting_for_address)
    await message.answer(
        f"✏️ Название: <b>{name}</b>\n\nТеперь введите адрес USDT TRC20-кошелька:",
        reply_markup=worker_cancel_withdrawal(),
    )


@router.message(CryptoAddressState.waiting_for_address)
async def process_addr_address(message: Message, db_user: User, state: FSMContext):
    from asgiref.sync import sync_to_async
    from apps.control.services import ControlWithdrawalService

    address = (message.text or "").strip()
    if not address or len(address) < 20:
        await message.answer(
            "❌ Некорректный адрес. Введите адрес USDT TRC20-кошелька:",
            reply_markup=worker_cancel_withdrawal(),
        )
        return

    data = await state.get_data()
    name = data.get("addr_name", "")
    await sync_to_async(ControlWithdrawalService.save_address)(db_user, name, address)
    await state.clear()
    await message.answer(
        f"✅ Адрес <b>{name}</b> сохранён:\n<code>{address}</code>",
        reply_markup=worker_back_to_menu(),
    )


# ── Report submission ──────────────────────────────────────────────────────────

@router.callback_query(CtrlWorkerCB.filter(F.action == "submit_report"))
async def cb_submit_report(callback: CallbackQuery, db_user: User, state: FSMContext):
    from asgiref.sync import sync_to_async
    from apps.control.services import ReportService

    # Telegram keeps the button spinner active until answerCallbackQuery. Do
    # this before database work so a slow query cannot look like a frozen bot.
    await callback.answer()

    # Get assigned templates (no global blocking — per-template blocking handled in submit_report)
    templates = await sync_to_async(ReportService.get_active_templates_for_user)(db_user)

    if len(templates) > 1:
        # Multiple templates — let worker pick
        await state.set_state(SubmitReportState.selecting_template)
        await callback.message.edit_text(
            "📝 <b>Подача отчёта</b>\n\nВыберите тип отчёта:",
            reply_markup=worker_template_select_keyboard(templates),
        )
        return

    # 0 or 1 template — go straight to submission
    template = templates[0] if templates else None
    await _start_report_input(callback.message, state, template)


@router.callback_query(CtrlWorkerCB.filter(F.action == "my_penalties"))
async def cb_my_penalties(callback: CallbackQuery, db_user: User):
    from asgiref.sync import sync_to_async
    from apps.control.services import PenaltyService

    penalties = await sync_to_async(
        lambda: list(PenaltyService.get_active_for_user(db_user)[:10])
    )()

    if not penalties:
        await callback.answer()
        await callback.message.edit_text(
            "✅ У вас нет активных штрафов.",
            reply_markup=worker_back_to_menu(),
        )
        return

    status_emoji = {
        "created": "🆕", "pending": "⏳", "accepted": "❌",
        "rejected": "✅", "disputed": "🔄", "deleted": "🗑",
    }
    text = "⚠️ <b>Ваши штрафы</b>\n\n"
    b = InlineKeyboardBuilder()
    for p in penalties:
        emoji = status_emoji.get(p.status, "❓")
        text += f"{emoji} <b>#{p.pk}</b> — {p.amount:.2f} ₽\n"
        text += f"   {p.reason}\n"
        text += f"   Статус: {p.get_status_display()}\n"
        if p.status in ("created", "pending", "accepted"):
            b.button(
                text=f"#{p.pk} Оспорить",
                callback_data=CtrlWorkerCB(action=f"dispute_{p.pk}"),
            )
        text += "\n"

    b.button(text="◀️ Назад", callback_data=CtrlWorkerCB(action="main_menu"))
    b.adjust(1)

    await callback.answer()
    await callback.message.edit_text(text, reply_markup=b.as_markup())


@router.callback_query(CtrlWorkerCB.filter())
async def cb_worker_dynamic(callback: CallbackQuery, callback_data: CtrlWorkerCB,
                             db_user: User, state: FSMContext):
    """Handle dynamic worker actions: template pick, dispute, address selection."""
    action = callback_data.action

    if action == "noop":
        await callback.answer()

    elif action.startswith("dl."):
        parts = action.split(".")
        selected_filter = parts[1] if len(parts) > 1 else "all"
        try:
            page = int(parts[2]) if len(parts) > 2 else 0
        except ValueError:
            page = 0
        await callback.answer()
        await _show_deadlines(callback, db_user, selected_filter, page)

    elif action.startswith("dli."):
        from asgiref.sync import sync_to_async
        from apps.control.deadlines import ReportDeadlineProvider

        parts = action.split(".")
        try:
            template_id = int(parts[1])
            report_date = dt.datetime.strptime(parts[2], "%Y%m%d").date()
            selected_filter = parts[3]
            page = int(parts[4])
        except (ValueError, IndexError):
            await callback.answer("Некорректный дедлайн", show_alert=True)
            return
        item = await sync_to_async(ReportDeadlineProvider.get_item)(db_user, template_id, report_date)
        if item is None:
            await callback.answer("Дедлайн больше не актуален", show_alert=True)
            return
        await callback.answer()
        await callback.message.edit_text(
            _deadline_detail_text(item),
            reply_markup=worker_deadline_detail_keyboard(item, selected_filter, page),
        )

    elif action.startswith("dle."):
        from asgiref.sync import sync_to_async
        from apps.control.deadlines import ReportDeadlineProvider
        from apps.control.models import ReportTemplate

        parts = action.split(".")
        try:
            template_id = int(parts[1])
            report_date = dt.datetime.strptime(parts[2], "%Y%m%d").date()
        except (ValueError, IndexError):
            await callback.answer("Некорректный дедлайн", show_alert=True)
            return
        item = await sync_to_async(ReportDeadlineProvider.get_item)(db_user, template_id, report_date)
        template = await sync_to_async(
            lambda: ReportTemplate.objects.filter(pk=template_id, assigned_users=db_user).first()
        )()
        if item is None or template is None or not item.can_edit:
            await callback.answer("Редактирование недоступно", show_alert=True)
            return
        await callback.answer()
        await _start_report_input(
            callback.message,
            state,
            template,
            report_date=report_date,
        )

    elif action.startswith("dlo."):
        from asgiref.sync import sync_to_async
        from apps.control.models import EmployeeReport

        try:
            report_id = int(action.split(".", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("Некорректный отчёт", show_alert=True)
            return
        report = await sync_to_async(
            lambda: EmployeeReport.objects.prefetch_related("media_files").filter(
                pk=report_id, user=db_user,
            ).first()
        )()
        if report is None:
            await callback.answer("Отчёт не найден", show_alert=True)
            return
        await callback.answer()
        await _send_report_snapshot(callback.message, report)

    elif action.startswith("pick_tmpl_"):
        try:
            tmpl_id = int(action.split("_", 2)[2])
        except (ValueError, IndexError):
            await callback.answer("Ошибка", show_alert=True)
            return

        from asgiref.sync import sync_to_async
        from apps.control.models import ReportTemplate

        template = await sync_to_async(
            lambda: ReportTemplate.objects.filter(pk=tmpl_id).first()
        )()
        if not template:
            await callback.answer("Шаблон не найден", show_alert=True)
            return

        await callback.answer()
        await _start_report_input(callback.message, state, template)

    elif action.startswith("edit_report_"):
        try:
            report_id = int(action.rsplit("_", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("Ошибка", show_alert=True)
            return
        from asgiref.sync import sync_to_async
        from apps.control.models import EmployeeReport

        report = await sync_to_async(
            lambda: EmployeeReport.objects.select_related("template").filter(
                pk=report_id,
                user=db_user,
            ).first()
        )()
        if report is None or not report.can_user_edit():
            await callback.answer("Период редактирования уже закончился", show_alert=True)
            return
        await callback.answer()
        await _start_report_input(callback.message, state, report.template, report=report)

    elif action == "close_notice":
        await callback.answer()
        try:
            await callback.message.delete()
        except Exception:
            await callback.message.edit_reply_markup(reply_markup=None)

    elif action == "finish_report":
        data = await state.get_data()
        report_id = data.get("active_report_id")
        if not report_id:
            await callback.answer("Сначала отправьте текст или файл", show_alert=True)
            return
        await state.clear()
        await callback.answer("Отчёт завершён")
        await callback.message.edit_text(
            f"✅ <b>Отчёт #{report_id} отправлен на проверку</b>\n\n"
            "Все приложенные файлы сохранены в CRM.",
            reply_markup=worker_back_to_menu(),
        )

    elif action == "report_more":
        await callback.answer("Отправьте следующий файл или текст")

    elif action.startswith("dispute_"):
        try:
            penalty_id = int(action.split("_", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("Ошибка", show_alert=True)
            return

        from asgiref.sync import sync_to_async
        from apps.control.models import Penalty

        penalty = await sync_to_async(Penalty.objects.filter(pk=penalty_id).first)()
        if not penalty or penalty.user_id != db_user.pk:
            await callback.answer("Штраф не найден", show_alert=True)
            return

        from apps.control.bot.states import DisputePenaltyState
        await state.set_state(DisputePenaltyState.waiting_for_comment)
        await state.update_data(penalty_id=penalty_id)
        await callback.answer()
        await callback.message.edit_text(
            f"✏️ <b>Оспаривание штрафа #{penalty_id}</b>\n\n"
            f"Причина: {penalty.reason}\n"
            f"Сумма: {penalty.amount:.2f} ₽\n\n"
            f"Напишите ваш комментарий для администратора:",
            reply_markup=worker_cancel_report(),
        )

    elif action == "add_addr":
        await state.set_state(CryptoAddressState.waiting_for_name)
        await callback.answer()
        await callback.message.edit_text(
            "➕ <b>Добавить адрес</b>\n\nВведите название адреса (например: «Основной», «Binance»):",
            reply_markup=worker_cancel_withdrawal(),
        )

    elif action == "addr_new":
        # User chose to enter address manually during withdrawal
        data = await state.get_data()
        amount_str = data.get("amount", "")
        await state.set_state(WithdrawalState.waiting_for_wallet)
        await callback.answer()
        await callback.message.edit_text(
            f"💳 <b>Адрес кошелька</b>\n\nСумма: <b>{amount_str} ₽</b>\n\nВведите адрес USDT TRC20-кошелька:",
            reply_markup=worker_cancel_withdrawal(),
        )

    elif action.startswith("addr_"):
        # User selected a saved address during withdrawal
        try:
            addr_id = int(action.split("_", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("Ошибка", show_alert=True)
            return

        from asgiref.sync import sync_to_async
        from apps.withdrawals.models import CryptoAddress

        addr_obj = await sync_to_async(
            lambda: CryptoAddress.objects.filter(pk=addr_id, user=db_user).first()
        )()
        if not addr_obj:
            await callback.answer("Адрес не найден", show_alert=True)
            return

        data = await state.get_data()
        amount_str = data.get("amount")
        from decimal import Decimal
        amount = Decimal(amount_str) if amount_str else None

        await callback.answer()
        await callback.message.delete()
        await _create_and_notify_withdrawal(callback.message, db_user, addr_obj.address, amount, state)

    elif action.startswith("del_addr_"):
        try:
            addr_id = int(action.split("_", 2)[2])
        except (ValueError, IndexError):
            await callback.answer("Ошибка", show_alert=True)
            return

        from asgiref.sync import sync_to_async
        from apps.control.services import ControlWithdrawalService

        await sync_to_async(ControlWithdrawalService.delete_address)(db_user, addr_id)
        await callback.answer("Адрес удалён")

        addresses = await sync_to_async(ControlWithdrawalService.get_saved_addresses)(db_user)
        if addresses:
            text = "💳 <b>Мои адреса</b>\n\nНажмите на адрес чтобы удалить его:"
        else:
            text = "💳 <b>Мои адреса</b>\n\nУ вас нет сохранённых адресов."
        await callback.message.edit_text(text, reply_markup=worker_addresses_list_keyboard(addresses))

    else:
        await callback.answer()


async def _start_report_input(
    message: Message,
    state: FSMContext,
    template=None,
    report=None,
    report_date: dt.date | None = None,
):
    """Enter the report input state for a given template (or generic if None)."""
    if template:
        await state.update_data(template_id=template.pk)
    else:
        await state.update_data(template_id=None)
    await state.update_data(report_id=report.pk if report else None)
    await state.update_data(report_date=report_date.isoformat() if report_date else None)
    await state.update_data(active_report_id=None)

    await state.set_state(SubmitReportState.waiting_for_report)

    text = "📝 <b>Подача отчёта</b>\n\n"
    if template and template.instructions:
        name = template.name or f"Шаблон #{template.pk}"
        text += f"<b>{name}</b>\n\n<b>Инструкции:</b>\n{template.instructions}\n\n"
    if report:
        text += f"Вы редактируете отчёт <b>#{report.pk}</b> за {report.report_date:%d.%m.%Y}.\n\n"
    text += (
        "Отправьте текст или первый файл. Поддерживаются фото, видео, "
        "документы, анимации, стикеры и видеосообщения. После этого можно "
        "добавить ещё файлы и завершить отчёт."
    )

    await message.edit_text(text, reply_markup=worker_cancel_report())


@router.message(SubmitReportState.waiting_for_report)
async def process_report_submission(message: Message, db_user: User, state: FSMContext):
    from asgiref.sync import sync_to_async
    from apps.control.services import ReportService
    from apps.control.models import ReportTemplate

    data = await state.get_data()
    template_id = data.get("template_id")
    report_id = data.get("report_id")
    report_date_raw = data.get("report_date")
    explicit_report_date = (
        dt.date.fromisoformat(report_date_raw)
        if report_date_raw
        else None
    )
    active_report_id = data.get("active_report_id")
    template = None
    if template_id:
        template = await sync_to_async(
            lambda: ReportTemplate.objects.filter(pk=template_id).first()
        )()

    # Detect content type
    text_content = ""
    file_id = ""
    file_type = "text"
    original_filename = ""

    media_object = None
    if message.document:
        media_object = message.document
        file_type = "document"
    elif message.photo:
        media_object = message.photo[-1]
        file_type = "photo"
    elif message.video:
        media_object = message.video
        file_type = "video"
    elif message.animation:
        media_object = message.animation
        file_type = "animation"
    elif message.sticker:
        media_object = message.sticker
        file_type = "sticker"
    elif message.video_note:
        media_object = message.video_note
        file_type = "video_note"

    if media_object:
        file_id = media_object.file_id
        original_filename = getattr(media_object, "file_name", "") or file_type
        text_content = message.caption or ""
    elif message.text:
        text_content = message.text
        file_type = "text"
    else:
        await message.answer(
            "❌ Неподдерживаемый тип. Аудио и голосовые сообщения нельзя прикреплять к отчёту.",
            reply_markup=worker_cancel_report(),
        )
        return

    try:
        if active_report_id:
            from apps.control.models import EmployeeReport
            report = await sync_to_async(
                lambda: EmployeeReport.objects.select_related("template").get(
                    pk=active_report_id,
                    user=db_user,
                )
            )()
            report = await sync_to_async(ReportService.append_to_current_revision)(
                report,
                db_user,
                text=text_content,
            )
        else:
            report = await sync_to_async(ReportService.submit_report)(
                user=db_user,
                template=template,
                text=text_content,
                file_id=file_id,
                file_type=file_type,
                original_filename=original_filename,
                report_id=report_id,
                explicit_report_date=explicit_report_date,
            )
    except ValueError as e:
        await state.clear()
        await message.answer(str(e), reply_markup=worker_back_to_menu())
        return

    if media_object:
        from apps.control.report_media import ReportMediaSaveError, save_message_attachment
        try:
            await save_message_attachment(message.bot, message, report)
        except ReportMediaSaveError as exc:
            await state.update_data(
                report_id=report.pk,
                active_report_id=report.pk,
            )
            await message.answer(
                f"⚠️ {exc}\nОтчёт сохранён; отправьте файл повторно или завершите отчёт.",
                reply_markup=worker_report_upload_keyboard(),
            )
            return

    await state.update_data(
        report_id=report.pk,
        active_report_id=report.pk,
    )
    from apps.control.models import ReportStatus
    is_resubmission = report.status == ReportStatus.UPDATED

    # Notify the right admin(s)
    from apps.users.models import UserRole, UserStatus
    from apps.control.bot.keyboards import admin_report_actions

    username = f"@{db_user.telegram_username}" if db_user.telegram_username else str(db_user.telegram_id)
    template_label = ""
    if template and template.name:
        template_label = f"\nТип: {template.name}"

    admin_text = (
        f"📋 <b>Новый отчёт от {username}</b>\n\n"
        f"Дата: {report.period_label}{template_label}\n"
        f"ID отчёта: #{report.pk}\n\n"
    )
    if text_content:
        admin_text += f"<b>Текст:</b>\n{text_content[:500]}"

    # Route: if template has created_by → notify only them; otherwise notify all admins
    if template and template.created_by_id:
        admin_ids = [template.created_by.telegram_id]
    else:
        admin_ids = await sync_to_async(
            lambda: list(
                User.objects.filter(
                    role=UserRole.ADMIN, status=UserStatus.ACTIVE, is_blocked_bot=False
                ).values_list("telegram_id", flat=True)
            )
        )()

    for admin_id in admin_ids:
        try:
            if file_id and file_type == "document":
                await message.bot.send_document(
                    admin_id,
                    document=file_id,
                    caption=admin_text,
                    parse_mode="HTML",
                    reply_markup=admin_report_actions(report.pk),
                )
            elif file_id and file_type == "photo":
                await message.bot.send_photo(
                    admin_id,
                    photo=file_id,
                    caption=admin_text,
                    parse_mode="HTML",
                    reply_markup=admin_report_actions(report.pk),
                )
            elif file_id and file_type in {"video", "animation", "video_note"}:
                await message.bot.send_video(
                    admin_id,
                    video=file_id,
                    caption=admin_text,
                    parse_mode="HTML",
                    reply_markup=admin_report_actions(report.pk),
                )
            else:
                await message.bot.send_message(
                    admin_id,
                    admin_text,
                    parse_mode="HTML",
                    reply_markup=admin_report_actions(report.pk),
                )
        except Exception:
            pass

    if is_resubmission:
        confirm_text = (
            f"🔄 <b>Отчёт обновлён</b>\n\n"
            f"Ваш исправленный отчёт за {report.period_label} отправлен на проверку.\n"
            "Файл сохранён. Можно отправить ещё один или завершить отчёт."
        )
    else:
        confirm_text = (
            f"✅ <b>Отчёт отправлен на проверку</b>\n\n"
            f"Ваш отчёт за {report.period_label} отправлен администратору.\n"
            "Можно отправить ещё один файл или завершить отчёт."
        )
    await message.answer(confirm_text, reply_markup=worker_report_upload_keyboard())


@router.message(DisputePenaltyState.waiting_for_comment)
async def process_dispute_comment(message: Message, db_user: User, state: FSMContext):
    from asgiref.sync import sync_to_async
    from apps.control.models import Penalty
    from apps.control.services import PenaltyService

    data = await state.get_data()
    penalty_id = data.get("penalty_id")
    penalty = await sync_to_async(Penalty.objects.filter(pk=penalty_id).first)()

    if not penalty:
        await state.clear()
        await message.answer("Штраф не найден.", reply_markup=worker_back_to_menu())
        return

    await sync_to_async(PenaltyService.dispute)(penalty, message.text or "")
    await state.clear()

    from apps.users.models import UserRole, UserStatus
    admin_ids = await sync_to_async(
        lambda: list(
            User.objects.filter(
                role=UserRole.ADMIN, status=UserStatus.ACTIVE, is_blocked_bot=False
            ).values_list("telegram_id", flat=True)
        )
    )()

    username = f"@{db_user.telegram_username}" if db_user.telegram_username else str(db_user.telegram_id)
    admin_text = (
        f"🔄 <b>Оспаривание штрафа #{penalty_id}</b>\n\n"
        f"Сотрудник: {username}\n"
        f"Штраф: {penalty.amount:.2f} ₽ — {penalty.reason}\n\n"
        f"<b>Комментарий сотрудника:</b>\n{message.text}"
    )
    from apps.control.bot.keyboards import admin_penalty_actions
    for admin_id in admin_ids:
        try:
            await message.bot.send_message(
                admin_id,
                admin_text,
                parse_mode="HTML",
                reply_markup=admin_penalty_actions(penalty_id),
            )
        except Exception:
            pass

    await message.answer(
        "✅ Ваш комментарий отправлен администратору. "
        "Он рассмотрит ваше обращение и примет решение.",
        reply_markup=worker_back_to_menu(),
    )
