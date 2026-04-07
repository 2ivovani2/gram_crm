"""
Admin: daily client data entry form.

FSM flow: link → client_nick → client_rate → total_applications → confirm → save
After save: triggers daily broadcast Celery task.
"""
import datetime
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.telegram_bot.admin_keyboards import (
    get_admin_cancel_keyboard, get_daily_report_confirm_keyboard, get_admin_main_menu,
)
from apps.telegram_bot.callbacks import AdminMenuCallback, AdminDailyCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import safe_edit_text
from apps.telegram_bot.states import AdminDailyReportState
from apps.users.models import User

router = Router(name="admin_daily")


def _preview_text(data: dict, computed: dict) -> str:
    total = data.get("total_applications", 0)
    wr = computed["worker_rate"]
    rr = computed["referral_rate"]
    op = computed["our_profit"]
    return (
        "📋 <b>Подтверждение данных за день</b>\n\n"
        f"🔗 Ссылка: {data['link']}\n"
        f"👤 Клиент: {data['client_nick']}\n"
        f"💵 Ставка клиента: <b>{data['client_rate']:.2f} ₽/чел.</b>\n"
        f"📝 Заявок: <b>{total} шт.</b>\n"
        "\n"
        "Расчёт по текущим долям:\n"
        f"  👷 Ставка работника:  <b>{wr:.2f} ₽/чел.</b> = <b>{(wr * total):.2f} ₽</b> всего\n"
        f"  🎓 Ставка реферала:   <b>{rr:.2f} ₽/чел.</b> = <b>{(rr * total):.2f} ₽</b> всего\n"
        f"  💼 Наша прибыль:      <b>{op:.2f} ₽/чел.</b> = <b>{(op * total):.2f} ₽</b> всего\n"
    )


# ── Entry ─────────────────────────────────────────────────────────────────────

@router.callback_query(AdminMenuCallback.filter(F.section == "daily"), IsAdmin())
async def cb_daily_section(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()

    from apps.stats.models import DailyReport, RateConfig
    today = datetime.date.today()
    existing = await sync_to_async(lambda: DailyReport.objects.filter(date=today).first())()
    config = await sync_to_async(RateConfig.get)()

    if existing:
        computed = config.compute(existing.client_rate)
        info = (
            f"ℹ️ Данные за сегодня уже внесены.\n\n"
            f"🔗 {existing.link or '—'}\n"
            f"👤 {existing.client_nick or '—'}\n"
            f"💵 {existing.client_rate:.2f} ₽/чел. · {existing.total_applications} шт.\n"
            f"📢 Рассылка: {'✅ отправлена' if existing.broadcast_sent else '⏳ ожидает'}\n\n"
            "Вы можете перезаписать данные, начав форму заново."
        )
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="✏️ Перезаписать", callback_data=AdminDailyCallback(action="start").pack()),
            InlineKeyboardButton(text="🔙 Главное меню", callback_data=AdminMenuCallback(section="main").pack()),
        )
        await safe_edit_text(callback, info, b.as_markup())
    else:
        await _start_form(callback, state, config)


@router.callback_query(AdminDailyCallback.filter(F.action == "start"), IsAdmin())
async def cb_daily_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    from apps.stats.models import RateConfig
    config = await sync_to_async(RateConfig.get)()
    await _start_form(callback, state, config)


async def _start_form(event, state: FSMContext, config) -> None:
    await state.update_data(
        worker_share=str(config.worker_share),
        referral_share=str(config.referral_share),
    )
    await state.set_state(AdminDailyReportState.waiting_for_link)
    text = (
        "📋 <b>Ввод данных за день</b>\n\n"
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
        await message.answer("⚠️ Ссылка должна начинаться с https:// (или «-» чтобы пропустить).",
                             reply_markup=get_admin_cancel_keyboard("main"))
        return
    await state.update_data(link=link)
    await state.set_state(AdminDailyReportState.waiting_for_client_nick)
    await message.answer("Шаг 2/4 — Введите <b>ник клиента</b> (или «-»):",
                         reply_markup=get_admin_cancel_keyboard("main"))


# ── Step 2: client nick ───────────────────────────────────────────────────────

@router.message(AdminDailyReportState.waiting_for_client_nick, IsAdmin())
async def process_daily_nick(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    await state.update_data(client_nick="" if raw == "-" else raw)
    await state.set_state(AdminDailyReportState.waiting_for_client_rate)
    await message.answer("Шаг 3/4 — Введите <b>ставку клиента</b> в руб./чел. (например: <code>3.6</code>):",
                         reply_markup=get_admin_cancel_keyboard("main"))


# ── Step 3: client rate ───────────────────────────────────────────────────────

@router.message(AdminDailyReportState.waiting_for_client_rate, IsAdmin())
async def process_daily_rate(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        rate = Decimal(raw)
        if rate <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ Введите положительное число (например: <code>3.6</code>).",
                             reply_markup=get_admin_cancel_keyboard("main"))
        return
    await state.update_data(client_rate=str(rate))
    await state.set_state(AdminDailyReportState.waiting_for_total_applications)
    await message.answer("Шаг 4/4 — Введите <b>количество заявок</b> за день (целое число ≥ 0):",
                         reply_markup=get_admin_cancel_keyboard("main"))


# ── Step 4: total applications + confirm ──────────────────────────────────────

@router.message(AdminDailyReportState.waiting_for_total_applications, IsAdmin())
async def process_daily_applications(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        total = int(raw)
        if total < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите целое число ≥ 0.", reply_markup=get_admin_cancel_keyboard("main"))
        return

    data = await state.get_data()
    client_rate = Decimal(data["client_rate"])
    worker_share = Decimal(data["worker_share"])
    referral_share = Decimal(data["referral_share"])

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
    )
    await message.answer(preview, reply_markup=get_daily_report_confirm_keyboard())


# ── Confirm ───────────────────────────────────────────────────────────────────

@router.callback_query(AdminDailyCallback.filter(F.action == "confirm"), IsAdmin())
async def cb_daily_confirm(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    await callback.answer()

    from apps.stats.services import DailyReportService
    report = await sync_to_async(DailyReportService.create_report)(
        date=datetime.date.today(),
        link=data.get("link", ""),
        client_nick=data.get("client_nick", ""),
        client_rate=Decimal(data["client_rate"]),
        total_applications=data["total_applications"],
        created_by=db_user,
    )

    # Trigger daily broadcast async
    from apps.stats.tasks import send_daily_broadcast_task
    send_daily_broadcast_task.delay(report.id)

    await safe_edit_text(
        callback,
        f"✅ <b>Данные за {report.date.strftime('%d.%m.%Y')} сохранены!</b>\n\n"
        f"📝 Заявок: <b>{report.total_applications}</b>\n"
        f"💵 Ставка клиента: <b>{report.client_rate:.2f} ₽</b>\n"
        f"📢 Рассылка поставлена в очередь.",
        get_admin_main_menu(),
    )
