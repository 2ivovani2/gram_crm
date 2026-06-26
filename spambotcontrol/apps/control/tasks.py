"""
Celery tasks for Gramly Control bot:
- send_report_reminders_task  — 3x daily (10:00, 15:00, 23:00 МСК)
- check_overdue_reports_task  — 23:30 МСК — auto-penalty for missing reports
"""
import asyncio
import logging

from celery import shared_task

logger = logging.getLogger(__name__)

REMINDER_TEXT = (
    "⏰ <b>Напоминание</b>\n\n"
    "Необходимо подать отчёт, иначе будет начислен штраф за просрочку."
)


def _send_message_sync(telegram_id: int, text: str) -> bool:
    """Send a Telegram message synchronously using the bot instance."""
    try:
        from apps.telegram_bot.bot import get_bot
        bot = get_bot()

        async def _send():
            try:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=text,
                    parse_mode="HTML",
                )
                return True
            except Exception as e:
                logger.warning(f"Failed to send reminder to {telegram_id}: {e}")
                return False

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_send())
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"Error sending message to {telegram_id}: {e}")
        return False


@shared_task(name="apps.control.tasks.send_report_reminders_task", queue="default")
def send_report_reminders_task():
    """Send report reminder to all active workers who haven't submitted a report today."""
    from apps.users.models import User, UserRole, UserStatus
    from apps.control.models import EmployeeReport, ReportStatus
    from django.utils import timezone

    today = timezone.localdate()

    # Workers who already submitted a report today (any status)
    submitted_today_ids = set(
        EmployeeReport.objects.filter(
            submitted_at__date=today
        ).values_list("user_id", flat=True)
    )

    workers = User.objects.filter(
        role=UserRole.WORKER,
        status=UserStatus.ACTIVE,
        is_blocked_bot=False,
    ).exclude(id__in=submitted_today_ids)

    sent = 0
    failed = 0
    for worker in workers:
        ok = _send_message_sync(worker.telegram_id, REMINDER_TEXT)
        if ok:
            sent += 1
        else:
            failed += 1

    logger.info(f"[control] Reminders sent: {sent}, failed: {failed}")
    return {"sent": sent, "failed": failed}


@shared_task(name="apps.control.tasks.check_overdue_reports_task", queue="default")
def check_overdue_reports_task():
    """
    Check for workers who haven't submitted a report today.
    Create auto-penalties and notify admin.
    """
    from apps.users.models import User, UserRole, UserStatus
    from apps.control.models import EmployeeReport, Penalty, PenaltyType, PenaltyStatus, ControlSettings
    from django.utils import timezone

    settings = ControlSettings.get()
    if settings.late_report_penalty_amount <= 0:
        logger.info("[control] Late report penalty amount is 0 — skipping auto-penalty")
        return {"skipped": True}

    today = timezone.localdate()

    submitted_today_ids = set(
        EmployeeReport.objects.filter(
            submitted_at__date=today,
            status__in=["pending", "accepted", "revision"],
        ).values_list("user_id", flat=True)
    )

    workers = User.objects.filter(
        role=UserRole.WORKER,
        status=UserStatus.ACTIVE,
    ).exclude(id__in=submitted_today_ids)

    created = 0
    admin_ids = list(
        User.objects.filter(role=UserRole.ADMIN, status=UserStatus.ACTIVE, is_blocked_bot=False)
        .values_list("telegram_id", flat=True)
    )

    for worker in workers:
        # Avoid duplicate auto-penalties for the same day
        already = Penalty.objects.filter(
            user=worker,
            type=PenaltyType.AUTO,
            created_at__date=today,
        ).exists()
        if already:
            continue

        penalty = Penalty.objects.create(
            user=worker,
            type=PenaltyType.AUTO,
            amount=settings.late_report_penalty_amount,
            reason=f"Просрочка подачи отчёта ({today.strftime('%-d %B %Y')})",
            status=PenaltyStatus.PENDING,
        )
        created += 1

        # Notify the worker
        worker_text = (
            f"🚨 <b>Начислен штраф за просрочку</b>\n\n"
            f"За неподанный отчёт {today.strftime('%-d %B %Y')} начислен штраф "
            f"<b>{penalty.amount:.2f} ₽</b>.\n\n"
            f"Для оспаривания обратитесь к администратору."
        )
        _send_message_sync(worker.telegram_id, worker_text)

        # Notify admins
        admin_text = (
            f"📋 <b>Автоштраф создан</b>\n\n"
            f"Сотрудник: @{worker.telegram_username or worker.telegram_id}\n"
            f"Причина: просрочка отчёта {today.strftime('%-d %B %Y')}\n"
            f"Сумма: <b>{penalty.amount:.2f} ₽</b>\n\n"
            f"Управление штрафами: /admin → Штрафы"
        )
        for admin_tg_id in admin_ids:
            _send_message_sync(admin_tg_id, admin_text)

    logger.info(f"[control] Auto-penalties created: {created}")
    return {"created": created}
