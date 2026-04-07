"""Admin: system statistics overview with weekly chart and financial summary."""
import datetime
from aiogram import Router, F
from aiogram.types import CallbackQuery
from asgiref.sync import sync_to_async

from apps.telegram_bot.admin_keyboards import get_stats_keyboard, get_admin_main_menu
from apps.telegram_bot.callbacks import AdminMenuCallback, AdminStatsCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import safe_edit_text
from apps.users.services import UserService

router = Router(name="admin_stats")


async def _build_stats_text() -> str:
    from apps.broadcasts.models import Broadcast, BroadcastStatus
    from apps.invites.models import InviteKey
    from apps.stats.models import DailyReport
    from apps.stats.services import DailyReportService
    from django.utils import timezone

    user_stats = await sync_to_async(UserService.get_stats_summary)()
    total_keys = await sync_to_async(InviteKey.objects.count)()
    active_keys = await sync_to_async(InviteKey.objects.filter(is_active=True).count)()
    total_broadcasts = await sync_to_async(Broadcast.objects.count)()
    running_broadcasts = await sync_to_async(
        Broadcast.objects.filter(status=BroadcastStatus.RUNNING).count
    )()

    today = datetime.date.today()
    today_report, week_reports, top_worker = await sync_to_async(
        lambda: (
            DailyReport.objects.filter(date=today).first(),
            DailyReportService.get_week_reports(),
            DailyReportService.get_top_worker_week(),
        )
    )()

    bar_chart = await sync_to_async(DailyReportService.build_weekly_bar_chart)(week_reports)
    fin_summary = await sync_to_async(DailyReportService.build_financial_summary)(today_report, week_reports)

    avg_applications = (
        round(sum(r.total_applications for r in week_reports) / len(week_reports), 1)
        if week_reports else 0
    )

    top_line = "—"
    if top_worker:
        user, count = top_worker
        top_line = f"<b>{user.display_name}</b> — {count} заявок"

    now_str = timezone.now().strftime("%d.%m.%Y %H:%M")

    return (
        f"📊 <b>Статистика системы</b>\n"
        f"<i>{now_str} UTC</i>\n"
        "\n"
        "👥 <b>Пользователи</b>\n"
        f"  Всего: <b>{user_stats['total']}</b>\n"
        f"  Активных: <b>{user_stats['active']}</b>\n"
        f"  Ожидают активации: <b>{user_stats['pending']}</b>\n"
        f"  Воркеров: <b>{user_stats['workers']}</b> · "
        f"Кураторов: <b>{user_stats['curators']}</b>\n"
        f"  Новых сегодня: <b>{user_stats['new_today']}</b>\n"
        "\n"
        "🔑 <b>Invite Keys</b>\n"
        f"  Всего: <b>{total_keys}</b> · Активных: <b>{active_keys}</b>\n"
        "\n"
        "📢 <b>Рассылки</b>\n"
        f"  Всего: <b>{total_broadcasts}</b> · Запущено: <b>{running_broadcasts}</b>\n"
        "\n"
        "📈 <b>Заявки — неделя (Пн → Вс)</b>\n"
        f"<code>{bar_chart}</code>\n"
        f"  Среднее/день: <b>{avg_applications}</b>\n"
        f"  Топ-1: {top_line}\n"
        "\n"
        f"{fin_summary}"
    )


@router.callback_query(AdminMenuCallback.filter(F.section == "stats"), IsAdmin())
async def cb_stats_section(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await _build_stats_text()
    await safe_edit_text(callback, text, get_stats_keyboard())


@router.callback_query(AdminStatsCallback.filter(F.action == "refresh"), IsAdmin())
async def cb_stats_refresh(callback: CallbackQuery) -> None:
    await callback.answer("Обновлено")
    text = await _build_stats_text()
    await safe_edit_text(callback, text, get_stats_keyboard())
