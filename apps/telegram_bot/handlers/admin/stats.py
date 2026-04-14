"""Admin: system statistics with period selector (today / this week / last week / month)."""
import datetime

from aiogram import Router, F
from aiogram.types import CallbackQuery
from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.telegram_bot.admin_keyboards import get_stats_keyboard
from apps.telegram_bot.callbacks import AdminMenuCallback, AdminStatsCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import safe_edit_text
from apps.users.services import UserService

router = Router(name="admin_stats")

_PERIOD_LABELS = {
    "today":     "сегодня",
    "week":      "эта неделя",
    "last_week": "прошлая неделя",
    "month":     "этот месяц",
}


async def _build_stats_text(period: str = "week") -> str:
    from apps.broadcasts.models import Broadcast, BroadcastStatus
    from apps.clients.services import JoinService
    from apps.stats.services import DailyReportService

    today = timezone.localdate()
    period_label = _PERIOD_LABELS.get(period, "эта неделя")
    start_date, end_date = await sync_to_async(
        DailyReportService.get_date_range_for_period
    )(period)

    (
        user_stats,
        pending_requests,
        total_broadcasts,
        running_broadcasts,
        reports,
        missed_count,
        top_worker,
    ) = await sync_to_async(lambda: (
        UserService.get_stats_summary(),
        JoinService.count_pending(),
        Broadcast.objects.count(),
        Broadcast.objects.filter(status=BroadcastStatus.RUNNING).count(),
        DailyReportService.get_reports_for_period(start_date, end_date),
        DailyReportService.count_missed_days(start_date, end_date),
        DailyReportService.get_top_worker_week(),
    ))()

    today_report = next((r for r in reports if r.date == today), None)
    now_str = timezone.localtime().strftime("%d.%m.%Y %H:%M")

    # ── Period-specific block ─────────────────────────────────────────────────
    if period == "today":
        apps_count = today_report.total_applications if today_report else 0
        has_data = "✅ внесены" if today_report else "🔴 не внесены"
        period_section = (
            f"📋 <b>Данные за сегодня — {has_data}</b>\n"
            f"  Заявок: <b>{apps_count}</b>\n"
        )
        fin_summary = await sync_to_async(DailyReportService.build_period_financial_summary)(
            [today_report] if today_report else []
        )

    elif period in ("week", "last_week"):
        if period == "week":
            week_start = today - datetime.timedelta(days=today.weekday())
        else:
            last_sunday = today - datetime.timedelta(days=today.weekday() + 1)
            week_start = last_sunday - datetime.timedelta(days=6)

        bar_chart = await sync_to_async(DailyReportService.build_weekly_bar_chart)(
            reports, week_start
        )
        avg = (
            round(sum(r.total_applications for r in reports) / len(reports), 1)
            if reports else 0
        )
        period_section = (
            f"📈 <b>Заявки — {period_label}</b>\n"
            f"<code>{bar_chart}</code>\n"
            f"  Среднее/день: <b>{avg}</b>\n"
        )
        fin_summary = await sync_to_async(DailyReportService.build_period_financial_summary)(reports)

    else:  # month
        total_apps = sum(r.total_applications for r in reports)
        period_section = (
            f"📆 <b>Заявки — {period_label}</b>\n"
            f"  Всего: <b>{total_apps}</b> · Дней с данными: <b>{len(reports)}</b>\n"
        )
        fin_summary = await sync_to_async(DailyReportService.build_period_financial_summary)(reports)

    missed_line = f"\n⚠️ Пропущено дней: <b>{missed_count}</b>" if missed_count > 0 else ""

    top_line = "—"
    if top_worker:
        user, count = top_worker
        top_line = f"<b>{user.display_name}</b> — {count} заявок"

    return (
        f"📊 <b>Статистика</b> — {period_label}\n"
        f"<i>{now_str} МСК</i>\n"
        "\n"
        "👥 <b>Пользователи</b>\n"
        f"  Всего: <b>{user_stats['total']}</b>\n"
        f"  Активных: <b>{user_stats['active']}</b>\n"
        f"  Ожидают: <b>{user_stats['pending']}</b>\n"
        f"  Воркеров: <b>{user_stats['workers']}</b> · "
        f"Кураторов: <b>{user_stats['curators']}</b>\n"
        f"  Новых сегодня: <b>{user_stats['new_today']}</b>\n"
        "\n"
        "📋 <b>Заявки на вступление</b>\n"
        f"  На рассмотрении: <b>{pending_requests}</b>\n"
        "\n"
        "📢 <b>Рассылки</b>\n"
        f"  Всего: <b>{total_broadcasts}</b> · Запущено: <b>{running_broadcasts}</b>\n"
        "\n"
        f"{period_section}"
        f"  Топ-1: {top_line}{missed_line}\n"
        "\n"
        f"{fin_summary}"
    )


@router.callback_query(AdminMenuCallback.filter(F.section == "stats"), IsAdmin())
async def cb_stats_section(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await _build_stats_text(period="week")
    await safe_edit_text(callback, text, get_stats_keyboard(period="week"))


@router.callback_query(AdminStatsCallback.filter(F.action == "refresh"), IsAdmin())
async def cb_stats_refresh(callback: CallbackQuery, callback_data: AdminStatsCallback) -> None:
    await callback.answer("Обновлено")
    period = callback_data.period or "week"
    text = await _build_stats_text(period=period)
    await safe_edit_text(callback, text, get_stats_keyboard(period=period))


@router.callback_query(AdminStatsCallback.filter(F.action == "period"), IsAdmin())
async def cb_stats_period(callback: CallbackQuery, callback_data: AdminStatsCallback) -> None:
    await callback.answer()
    period = callback_data.period or "week"
    text = await _build_stats_text(period=period)
    await safe_edit_text(callback, text, get_stats_keyboard(period=period))
