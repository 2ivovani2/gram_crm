"""Admin: system statistics overview."""
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
    from django.utils import timezone

    user_stats = await sync_to_async(UserService.get_stats_summary)()
    total_keys = await sync_to_async(InviteKey.objects.count)()
    active_keys = await sync_to_async(InviteKey.objects.filter(is_active=True).count)()
    total_broadcasts = await sync_to_async(Broadcast.objects.count)()
    running_broadcasts = await sync_to_async(
        Broadcast.objects.filter(status=BroadcastStatus.RUNNING).count
    )()

    return (
        "📊 <b>Статистика системы</b>\n"
        f"<i>{timezone.now().strftime('%d.%m.%Y %H:%M')} UTC</i>\n"
        "\n"
        "👥 <b>Пользователи</b>\n"
        f"  Всего: <b>{user_stats['total']}</b>\n"
        f"  Активных: <b>{user_stats['active']}</b>\n"
        f"  Ожидают активации: <b>{user_stats['pending']}</b>\n"
        f"  Заблокировано: <b>{user_stats['banned']}</b>\n"
        f"  Новых сегодня: <b>{user_stats['new_today']}</b>\n"
        f"  Администраторов: <b>{user_stats['admins']}</b>\n"
        "\n"
        "🔑 <b>Invite Keys</b>\n"
        f"  Всего: <b>{total_keys}</b>\n"
        f"  Активных: <b>{active_keys}</b>\n"
        "\n"
        "📢 <b>Рассылки</b>\n"
        f"  Всего: <b>{total_broadcasts}</b>\n"
        f"  Запущено сейчас: <b>{running_broadcasts}</b>\n"
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
