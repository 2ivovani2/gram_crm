"""
Admin: daily client data entry form.

FSM flow: [date selection] → link → client_nick → client_rate → total_applications → confirm → save

Date selection:
  - Default: today's date
  - "Внести за другой день": shows last 7 days without a DailyReport
  - Missed days (MissedDay records) are highlighted with 🔴

After save:
  - Triggers daily broadcast Celery task
  - If date had a MissedDay record → automatically marks it as filled (via DailyReportService)
"""
import datetime
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.telegram_bot.admin_keyboards import (
    get_admin_cancel_keyboard,
    get_daily_report_confirm_keyboard,
    get_admin_main_menu,
    get_daily_entry_menu_keyboard,
    get_daily_date_picker_keyboard,
)
from apps.telegram_bot.callbacks import AdminMenuCallback, AdminDailyCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import safe_edit_text
from apps.telegram_bot.states import AdminDailyReportState
from apps.users.models import User

router = Router(name="admin_daily")


def _preview_text(data: dict, computed: dict, report_date: datetime.date) -> str:
    total = data.get("total_applications", 0)
    wr = computed["worker_rate"]
    rr = computed["referral_rate"]
    op = computed["our_profit"]
    date_label = report_date.strftime("%d.%m.%Y")
    today = timezone.localdate()
    backdated_note = " <i>(задним числом)</i>" if report_date < today else ""
    return (
        f"📋 <b>Подтверждение данных за {date_label}{backdated_note}</b>\n\n"
        f"🔗 Ссылка: {data.get('link') or '—'}\n"
        f"👤 Клиент: {data.get('client_nick') or '—'}\n"
        f"💵 Ставка клиента: <b>{data['client_rate']:.2f} ₽/чел.</b>\n"
        f"📝 Заявок: <b>{total} шт.</b>\n"
        "\n"
        "Расчёт по текущим долям:\n"
        f"  👷 Ставка работника:  <b>{wr:.2f} ₽/чел.</b> = <b>{(wr * total):.2f} ₽</b> всего\n"
        f"  🎓 Ставка реферала:   <b>{rr:.2f} ₽/чел.</b> = <b>{(rr * total):.2f} ₽</b> всего\n"
        f"  💼 Наша прибыль:      <b>{op:.2f} ₽/чел.</b> = <b>{(op * total):.2f} ₽</b> всего\n"
    )


# ── Entry point ───────────────────────────────────────────────────────────────

@router.callback_query(AdminMenuCallback.filter(F.section == "daily"), IsAdmin())
async def cb_daily_section(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()

    from apps.stats.models import DailyReport, RateConfig
    from apps.stats.services import DailyReportService

    today = timezone.localdate()
    existing_today = await sync_to_async(
        lambda: DailyReport.objects.filter(date=today).first()
    )()
    other_dates = await sync_to_async(DailyReportService.get_unfilled_recent_dates)(7)
    has_other_dates = bool(other_dates)

    if existing_today:
        info = (
            f"ℹ️ Данные за <b>{today.strftime('%d.%m.%Y')}</b> уже внесены.\n\n"
            f"🔗 {existing_today.link or '—'}\n"
            f"👤 {existing_today.client_nick or '—'}\n"
            f"💵 {existing_today.client_rate:.2f} ₽/чел. · {existing_today.total_applications} шт.\n"
            f"📢 Рассылка: {'✅ отправлена' if existing_today.broadcast_sent else '⏳ ожидает'}\n\n"
            "Вы можете перезаписать данные или внести за другой день."
        )
        b = InlineKeyboardBuilder()
        b.row(InlineKeyboardButton(
            text="✏️ Перезаписать сегодня",
            callback_data=AdminDailyCallback(action="start").pack(),
        ))
        if has_other_dates:
            b.row(InlineKeyboardButton(
                text="📅 Внести за другой день",
                callback_data=AdminDailyCallback(action="pick_date").pack(),
            ))
        b.row(InlineKeyboardButton(
            text="🔙 Главное меню",
            callback_data=AdminMenuCallback(section="main").pack(),
        ))
        await safe_edit_text(callback, info, b.as_markup())
    else:
        text = (
            f"📋 <b>Ввод данных</b>\n\n"
            f"Данные за <b>{today.strftime('%d.%m.%Y')}</b> ещё не внесены.\n"
            "Выберите действие:"
        )
        await safe_edit_text(callback, text, get_daily_entry_menu_keyboard(has_other_dates))


# ── Date picker ───────────────────────────────────────────────────────────────

@router.callback_query(AdminDailyCallback.filter(F.action == "pick_date"), IsAdmin())
async def cb_daily_pick_date(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()

    from apps.stats.services import DailyReportService
    available = await sync_to_async(DailyReportService.get_unfilled_recent_dates)(7)

    if not available:
        b = InlineKeyboardBuilder()
        b.row(InlineKeyboardButton(
            text="🔙 Назад", callback_data=AdminMenuCallback(section="daily").pack()
        ))
        await safe_edit_text(
            callback,
            "✅ За все последние 7 дней данные уже внесены.",
            b.as_markup(),
        )
        return

    missed_set = await sync_to_async(DailyReportService.get_missed_dates_set)(available)
    await safe_edit_text(
        callback,
        "📅 <b>Выберите дату</b> для внесения данных:",
        get_daily_date_picker_keyboard(available, missed_set),
    )


@router.callback_query(AdminDailyCallback.filter(F.action == "select_date"), IsAdmin())
async def cb_daily_select_date(
    callback: CallbackQuery,
    callback_data: AdminDailyCallback,
    state: FSMContext,
) -> None:
    await callback.answer()

    try:
        selected_date = datetime.date.fromisoformat(callback_data.date_str)
    except (ValueError, AttributeError):
        await callback.answer("Некорректная дата", show_alert=True)
        return

    from apps.stats.models import RateConfig
    config = await sync_to_async(RateConfig.get)()
    await _start_form(callback, state, config, report_date=selected_date)


# ── Start form ────────────────────────────────────────────────────────────────

@router.callback_query(AdminDailyCallback.filter(F.action == "start"), IsAdmin())
async def cb_daily_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    from apps.stats.models import RateConfig
    config = await sync_to_async(RateConfig.get)()
    await _start_form(callback, state, config, report_date=timezone.localdate())


async def _start_form(
    event,
    state: FSMContext,
    config,
    report_date: datetime.date = None,
) -> None:
    if report_date is None:
        report_date = timezone.localdate()

    await state.update_data(
        worker_share=str(config.worker_share),
        referral_share=str(config.referral_share),
        report_date=report_date.isoformat(),
    )
    await state.set_state(AdminDailyReportState.waiting_for_link)

    today = timezone.localdate()
    date_label = report_date.strftime("%d.%m.%Y")
    backdated_note = " <i>(задним числом)</i>" if report_date < today else ""

    text = (
        f"📋 <b>Ввод данных за {date_label}{backdated_note}</b>\n\n"
        f"Текущие доли: работник <b>{float(config.worker_share)*100:.1f}%</b>, "
        f"реферал <b>{float(config.referral_share)*100:.1f}%</b>\n\n"
        "Шаг 1/4 — Отправьте <b>ссылку</b> на пост/канал (или «-» чтобы пропустить):"
    )
    kb = get_admin_cancel_keyboard("main")
    if isinstance(event, CallbackQuery):
        await safe_edit_text(event, text, kb)
    else:
        await event.answer(text, reply_markup=kb)


# ── Step 1: link ──────────────────────────────────────────────────────────────

@router.message(AdminDailyReportState.waiting_for_link, IsAdmin())
async def process_daily_link(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    link = "" if raw == "-" else raw
    if link and not (link.startswith("http://") or link.startswith("https://")):
        await message.answer(
            "⚠️ Ссылка должна начинаться с https:// (или «-» чтобы пропустить).",
            reply_markup=get_admin_cancel_keyboard("main"),
        )
        return
    await state.update_data(link=link)
    await state.set_state(AdminDailyReportState.waiting_for_client_nick)
    await message.answer(
        "Шаг 2/4 — Введите <b>ник клиента</b> (или «-»):",
        reply_markup=get_admin_cancel_keyboard("main"),
    )


# ── Step 2: client nick ───────────────────────────────────────────────────────

@router.message(AdminDailyReportState.waiting_for_client_nick, IsAdmin())
async def process_daily_nick(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    await state.update_data(client_nick="" if raw == "-" else raw)
    await state.set_state(AdminDailyReportState.waiting_for_client_rate)
    await message.answer(
        "Шаг 3/4 — Введите <b>ставку клиента</b> в руб./чел. (например: <code>3.6</code>):",
        reply_markup=get_admin_cancel_keyboard("main"),
    )


# ── Step 3: client rate ───────────────────────────────────────────────────────

@router.message(AdminDailyReportState.waiting_for_client_rate, IsAdmin())
async def process_daily_rate(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        rate = Decimal(raw)
        if rate <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer(
            "⚠️ Введите положительное число (например: <code>3.6</code>).",
            reply_markup=get_admin_cancel_keyboard("main"),
        )
        return
    await state.update_data(client_rate=str(rate))
    await state.set_state(AdminDailyReportState.waiting_for_total_applications)
    await message.answer(
        "Шаг 4/4 — Введите <b>количество заявок</b> за день (целое число ≥ 0):",
        reply_markup=get_admin_cancel_keyboard("main"),
    )


# ── Step 4: total applications + confirm ──────────────────────────────────────

@router.message(AdminDailyReportState.waiting_for_total_applications, IsAdmin())
async def process_daily_applications(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        total = int(raw)
        if total < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "⚠️ Введите целое число ≥ 0.",
            reply_markup=get_admin_cancel_keyboard("main"),
        )
        return

    data = await state.get_data()
    client_rate = Decimal(data["client_rate"])
    worker_share = Decimal(data["worker_share"])
    referral_share = Decimal(data["referral_share"])
    report_date = datetime.date.fromisoformat(
        data.get("report_date", timezone.localdate().isoformat())
    )

    worker_rate = (client_rate * worker_share).quantize(Decimal("0.01"))
    referral_rate = (client_rate * referral_share).quantize(Decimal("0.01"))
    our_profit = (client_rate - worker_rate - referral_rate).quantize(Decimal("0.01"))

    await state.update_data(
        total_applications=total,
        worker_rate=str(worker_rate),
        referral_rate=str(referral_rate),
        our_profit=str(our_profit),
    )
    await state.set_state(AdminDailyReportState.confirm)

    preview = _preview_text(
        {**data, "client_rate": client_rate, "total_applications": total},
        {"worker_rate": worker_rate, "referral_rate": referral_rate, "our_profit": our_profit},
        report_date,
    )
    await message.answer(preview, reply_markup=get_daily_report_confirm_keyboard())


# ── Confirm ───────────────────────────────────────────────────────────────────

@router.callback_query(AdminDailyCallback.filter(F.action == "confirm"), IsAdmin())
async def cb_daily_confirm(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    await callback.answer()

    report_date = datetime.date.fromisoformat(
        data.get("report_date", timezone.localdate().isoformat())
    )

    from apps.stats.services import DailyReportService
    report = await sync_to_async(DailyReportService.create_report)(
        date=report_date,
        link=data.get("link", ""),
        client_nick=data.get("client_nick", ""),
        client_rate=Decimal(data["client_rate"]),
        total_applications=data["total_applications"],
        created_by=db_user,
    )

    from apps.stats.tasks import send_daily_broadcast_task
    send_daily_broadcast_task.delay(report.id)

    today = timezone.localdate()
    is_backdated = report.date < today
    backdated_note = " <i>(задним числом)</i>" if is_backdated else ""

    await safe_edit_text(
        callback,
        f"✅ <b>Данные за {report.date.strftime('%d.%m.%Y')} сохранены!{backdated_note}</b>\n\n"
        f"📝 Заявок: <b>{report.total_applications}</b>\n"
        f"💵 Ставка клиента: <b>{report.client_rate:.2f} ₽</b>\n"
        f"📢 Рассылка поставлена в очередь.",
        get_admin_main_menu(),
    )
