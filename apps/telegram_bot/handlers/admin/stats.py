"""Admin: system statistics — client/link/worker based (no DailyReport)."""
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


async def _build_stats_text() -> str:
    from apps.broadcasts.models import Broadcast, BroadcastStatus
    from apps.clients.models import Client, ClientLink, LinkAssignment, LinkStatus
    from apps.clients.services import JoinService
    from apps.stats.models import GlobalRate
    from apps.users.models import User, UserRole, UserStatus, WorkLink
    from django.db.models import Sum, Q, Count

    now_str = timezone.localtime().strftime("%d.%m.%Y %H:%M")

    (
        user_stats,
        pending_requests,
        total_broadcasts,
        running_broadcasts,
        total_clients,
        active_links,
        active_assignments,
        total_applications,
        global_rate,
        top_worker,
        idle_count,
    ) = await sync_to_async(lambda: (
        UserService.get_stats_summary(),
        JoinService.count_pending(),
        Broadcast.objects.count(),
        Broadcast.objects.filter(status=BroadcastStatus.RUNNING).count(),
        Client.objects.count(),
        ClientLink.objects.filter(status=LinkStatus.ACTIVE).count(),
        LinkAssignment.objects.filter(is_active=True).count(),
        WorkLink.objects.aggregate(total=Sum("attracted_count"))["total"] or 0,
        GlobalRate.get(),
        (
            User.objects
            .filter(role__in=[UserRole.WORKER, UserRole.CURATOR], attracted_count__gt=0)
            .order_by("-attracted_count")
            .first()
        ),
        (
            User.objects
            .filter(
                role__in=[UserRole.WORKER, UserRole.CURATOR],
                status=UserStatus.ACTIVE,
                is_activated=True,
            )
            .annotate(active_count=Count(
                "link_assignments",
                filter=Q(link_assignments__is_active=True),
            ))
            .filter(active_count=0)
            .count()
        ),
    ))()

    from decimal import Decimal
    worker_payout = (Decimal(total_applications) * global_rate.worker_rate).quantize(Decimal("0.01"))
    referral_payout = (Decimal(total_applications) * global_rate.referral_rate).quantize(Decimal("0.01"))

    top_line = "—"
    if top_worker:
        top_line = f"<b>{top_worker.display_name}</b> — {top_worker.attracted_count} заявок"

    return (
        f"📊 <b>Статистика</b>\n"
        f"<i>{now_str} МСК</i>\n"
        "\n"
        "👥 <b>Пользователи</b>\n"
        f"  Всего: <b>{user_stats['total']}</b>\n"
        f"  Активных: <b>{user_stats['active']}</b>\n"
        f"  На рассмотрении: <b>{user_stats['pending']}</b>\n"
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
        "🔗 <b>Клиенты и ссылки</b>\n"
        f"  Клиентов: <b>{total_clients}</b>\n"
        f"  Активных ссылок: <b>{active_links}</b> · Назначений: <b>{active_assignments}</b>\n"
        f"  Воркеров без ссылки: <b>{idle_count}</b>\n"
        "\n"
        "📝 <b>Заявки</b>\n"
        f"  Всего (все ссылки): <b>{total_applications}</b>\n"
        f"  Долг воркерам: <b>{worker_payout} ₽</b> · Долг рефам: <b>{referral_payout} ₽</b>\n"
        f"  Ставки: воркер <b>{global_rate.worker_rate} ₽</b> · реферал <b>{global_rate.referral_rate} ₽</b>\n"
        "\n"
        f"🏆 Топ-1: {top_line}"
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
