"""Worker: personal cabinet (profile) view."""
from aiogram import Router, F
from aiogram.types import CallbackQuery
from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.telegram_bot.callbacks import WorkerCallback
from apps.telegram_bot.keyboards import get_profile_keyboard
from apps.telegram_bot.permissions import IsActivatedWorker
from apps.telegram_bot.services import answer_and_edit
from apps.users.models import User

router = Router(name="worker_profile")


def _format_profile(user: User, referral_count: int, referral_url: str, breakdown: dict, daily_stats=None) -> str:
    status_icon = {"active": "✅", "inactive": "⛔", "pending": "⏳", "banned": "🚫"}.get(user.status, "❓")

    # WorkLink history summary
    total_attracted = breakdown["total_attracted"]
    active_attracted = breakdown["active_attracted"]
    archived_attracted = total_attracted - active_attracted

    lines = [
        "👤 <b>Личный кабинет</b>",
        "",
        f"🆔 ID: <code>{user.telegram_id}</code>",
        f"👤 Имя: <b>{user.display_name}</b>",
        f"📌 Статус: {status_icon} {user.get_status_display()}",
        "",
        "💼 <b>Начисления</b>",
        f"  👤 Личное (своя ссылка):   <b>{breakdown['personal_earned']:.2f} ₽</b>",
        f"  🤝 Реферальное:            <b>{breakdown['referral_earned']:.2f} ₽</b>",
        f"  ─────────────────────────────",
        f"  📊 Начислено всего:        <b>{breakdown['gross_earned']:.2f} ₽</b>",
        f"  💸 Выведено:               <b>{breakdown['withdrawn']:.2f} ₽</b>",
        f"  ✅ Доступно к выводу:      <b>{breakdown['balance']:.2f} ₽</b>",
        "",
        "📈 <b>Привлечённые</b>",
        f"  По активной ссылке:        <b>{active_attracted}</b>",
    ]

    if archived_attracted > 0:
        lines.append(f"  По старым ссылкам (архив): <b>{archived_attracted}</b>")

    lines += [
        f"  Итого привлечено:          <b>{total_attracted}</b>",
        f"  💰 Личная ставка:          <b>{user.personal_rate:.2f} руб./чел.</b>",
        "",
        f"👥 Рефералов в структуре:    <b>{referral_count}</b>",
    ]

    if breakdown['referrals_total_attracted'] > 0:
        lines.append(f"  Привлечено рефами:         <b>{breakdown['referrals_total_attracted']}</b>")
        lines.append(f"  🤝 Ставка за рефералов:    <b>{user.referral_rate:.2f} руб./чел.</b>")

    if user.work_url:
        lines += ["", f"🔗 <b>Активная ссылка:</b>", f"<code>{user.work_url}</code>"]

    lines += [
        "",
        "🤝 <b>Ваша реферальная ссылка:</b>",
        f"<code>{referral_url}</code>",
    ]

    if daily_stats:
        lines += [
            "",
            "📊 <b>Сегодня:</b>",
            f"  📝 Заявок: <b>{daily_stats.tasks_submitted}</b>",
            f"  ✅ Выполнено: <b>{daily_stats.tasks_completed}</b>",
            f"  📈 Выполнение: <b>{daily_stats.completion_rate}%</b>",
        ]

    if user.activated_at:
        lines += ["", f"📅 Активирован: {user.activated_at.strftime('%d.%m.%Y')}"]

    return "\n".join(lines)


@router.callback_query(WorkerCallback.filter(F.action == "profile"), IsActivatedWorker())
async def cb_profile(callback: CallbackQuery, db_user: User) -> None:
    from apps.stats.models import UserDailyStats
    from apps.referrals.services import ReferralService
    from apps.users.services import UserService

    today = timezone.now().date()

    daily_stats, referral_count, referral_url, breakdown = await sync_to_async(
        lambda: (
            UserDailyStats.objects.filter(user=db_user, date=today).first(),
            db_user.referrals.count(),
            ReferralService.get_referral_url(db_user),
            UserService.get_earnings_breakdown(db_user),
        )
    )()

    from django.conf import settings
    channels_url = getattr(settings, "CHANNELS_DB_URL", "")
    text = _format_profile(db_user, referral_count, referral_url, breakdown, daily_stats)
    await answer_and_edit(callback, text, get_profile_keyboard(channels_db_url=channels_url))


@router.callback_query(WorkerCallback.filter(F.action == "stats"), IsActivatedWorker())
async def cb_stats(callback: CallbackQuery, db_user: User) -> None:
    from apps.stats.models import UserDailyStats

    recent = await sync_to_async(
        lambda: list(UserDailyStats.objects.filter(user=db_user).order_by("-date")[:7])
    )()

    if not recent:
        text = "📊 <b>Статистика</b>\n\nДанных пока нет."
    else:
        lines = ["📊 <b>Статистика за 7 дней</b>", ""]
        for stat in recent:
            lines.append(
                f"<b>{stat.date.strftime('%d.%m')}</b> — "
                f"{stat.tasks_completed}/{stat.tasks_submitted} ({stat.completion_rate}%)"
            )
        text = "\n".join(lines)

    from apps.telegram_bot.keyboards import get_back_to_start_keyboard
    await answer_and_edit(callback, text, get_back_to_start_keyboard())
