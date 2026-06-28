"""
Celery tasks for Gramly Control bot:
- send_report_reminders_task      — template-aware reminders (per notification_times)
- check_overdue_reports_task      — 23:30 МСК — auto-penalty for workers without any report today
- check_correction_deadlines_task — every hour — auto-penalty for expired rejection deadlines
"""
import asyncio
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


def _send_message_sync(telegram_id: int, text: str) -> bool:
    """Send a Telegram message synchronously using a fresh bot instance."""
    try:
        from apps.control.bot.invite_handlers import _make_bot

        async def _send():
            bot = _make_bot()
            try:
                await bot.send_message(chat_id=telegram_id, text=text, parse_mode="HTML")
                return True
            except Exception as e:
                logger.warning("Failed to send message to %s: %s", telegram_id, e)
                return False
            finally:
                await bot.session.close()

        return asyncio.run(_send())
    except Exception as e:
        logger.error("Error sending message to %s: %s", telegram_id, e)
        return False


@shared_task(name="apps.control.tasks.send_report_reminders_task", queue="default")
def send_report_reminders_task():
    """
    Send report reminders to active workers who haven't submitted today.
    For workers with assigned templates: per-template reminder text.
    For others: generic reminder.
    """
    from apps.users.models import User, UserRole, UserStatus
    from apps.control.models import EmployeeReport, ReportTemplate
    from django.utils import timezone

    today = timezone.localdate()

    # Workers who already have a report today (any blocking status)
    submitted_today_ids = set(
        EmployeeReport.objects.filter(
            submitted_at__date=today,
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
        # Get assigned templates for custom reminder text
        templates = list(
            ReportTemplate.objects.filter(assigned_users=worker).order_by("name")
        )
        if templates:
            tmpl_names = ", ".join(t.name for t in templates if t.name) or "шаблон отчёта"
            text = (
                f"⏰ <b>Напоминание об отчёте</b>\n\n"
                f"Не забудьте сдать отчёт: <b>{tmpl_names}</b>\n\n"
                f"Нажмите «📝 Подать отчёт» в меню."
            )
        else:
            text = (
                "⏰ <b>Напоминание</b>\n\n"
                "Необходимо подать отчёт, иначе будет начислен штраф за просрочку."
            )

        ok = _send_message_sync(worker.telegram_id, text)
        if ok:
            sent += 1
        else:
            failed += 1

    logger.info("[control] Reminders sent: %d, failed: %d", sent, failed)
    return {"sent": sent, "failed": failed}


@shared_task(name="apps.control.tasks.check_overdue_reports_task", queue="default")
def check_overdue_reports_task():
    """
    Check for workers who haven't submitted ANY report today.
    Create auto-penalties using global ControlSettings.late_report_penalty_amount.
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
        already = Penalty.objects.filter(
            user=worker,
            type=PenaltyType.AUTO,
            created_at__date=today,
            reason__startswith="Просрочка подачи отчёта",
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

        worker_text = (
            f"🚨 <b>Начислен штраф за просрочку</b>\n\n"
            f"За неподанный отчёт {today.strftime('%-d %B %Y')} начислен штраф "
            f"<b>{penalty.amount:.2f} ₽</b>.\n\n"
            f"Для оспаривания обратитесь к администратору."
        )
        _send_message_sync(worker.telegram_id, worker_text)

        admin_text = (
            f"📋 <b>Автоштраф создан</b>\n\n"
            f"Сотрудник: @{worker.telegram_username or worker.telegram_id}\n"
            f"Причина: просрочка отчёта {today.strftime('%-d %B %Y')}\n"
            f"Сумма: <b>{penalty.amount:.2f} ₽</b>"
        )
        for admin_tg_id in admin_ids:
            _send_message_sync(admin_tg_id, admin_text)

    logger.info("[control] Auto-penalties created: %d", created)
    return {"created": created}


@shared_task(name="apps.control.tasks.check_correction_deadlines_task", queue="default")
def check_correction_deadlines_task():
    """
    Mark as OVERDUE rejected reports whose correction_deadline has passed.
    Create auto-penalty from template.auto_penalty_amount if > 0.
    """
    from apps.control.models import (
        EmployeeReport, ReportStatus, Penalty, PenaltyType, PenaltyStatus,
    )
    from apps.users.models import User, UserRole, UserStatus
    from django.utils import timezone

    now = timezone.now()
    today_str = timezone.localdate().strftime("%-d %B %Y")

    expired = list(
        EmployeeReport.objects.select_related("user", "template")
        .filter(
            status=ReportStatus.REJECTED,
            correction_deadline__lt=now,
            correction_deadline__isnull=False,
        )
    )

    penalized = 0
    admin_ids = list(
        User.objects.filter(role=UserRole.ADMIN, status=UserStatus.ACTIVE, is_blocked_bot=False)
        .values_list("telegram_id", flat=True)
    )

    for report in expired:
        # Mark overdue
        report.status = ReportStatus.OVERDUE
        report.save(update_fields=["status", "updated_at"])

        # Create auto-penalty if template has configured amount
        penalty_amount = None
        if report.template and report.template.auto_penalty_amount > 0:
            penalty_amount = report.template.auto_penalty_amount

        if penalty_amount:
            already = Penalty.objects.filter(
                user=report.user,
                type=PenaltyType.AUTO,
                report=report,
            ).exists()
            if not already:
                reason = f"Не исправил отчёт в срок ({report.period_label or today_str})"
                Penalty.objects.create(
                    user=report.user,
                    type=PenaltyType.AUTO,
                    amount=penalty_amount,
                    reason=reason,
                    status=PenaltyStatus.PENDING,
                    report=report,
                )
                penalized += 1

                worker_text = (
                    f"🚨 <b>Штраф за просроченное исправление</b>\n\n"
                    f"Вы не исправили отчёт за {report.period_label} в установленный срок.\n"
                    f"Начислен штраф: <b>{penalty_amount:.2f} ₽</b>.\n\n"
                    f"Для оспаривания обратитесь к администратору."
                )
                _send_message_sync(report.user.telegram_id, worker_text)

                admin_text = (
                    f"📋 <b>Автоштраф за просроченное исправление</b>\n\n"
                    f"Сотрудник: @{report.user.telegram_username or report.user.telegram_id}\n"
                    f"Отчёт #{report.pk} ({report.period_label})\n"
                    f"Сумма: <b>{penalty_amount:.2f} ₽</b>"
                )
                for admin_id in admin_ids:
                    _send_message_sync(admin_id, admin_text)

        # Always notify worker that report is overdue (even without penalty)
        else:
            worker_text = (
                f"⏰ <b>Срок исправления истёк</b>\n\n"
                f"Отчёт за {report.period_label} помечен как просроченный.\n"
                f"Обратитесь к администратору."
            )
            _send_message_sync(report.user.telegram_id, worker_text)

    logger.info("[control] Correction deadlines expired: %d, penalized: %d", len(expired), penalized)
    return {"expired": len(expired), "penalized": penalized}
