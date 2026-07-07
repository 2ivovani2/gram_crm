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
    Runs every hour. Sends report reminders based on per-template notification_times.

    For each template that has notification_times configured: if the current MSK hour
    matches one of those times, notify all assigned workers who haven't submitted today.

    For workers not assigned to any template: send at default times 10:00, 15:00, 23:00.
    """
    import datetime as dt
    from zoneinfo import ZoneInfo
    from apps.users.models import User, UserRole, UserStatus
    from apps.control.models import EmployeeReport, ReportTemplate

    _MSK = ZoneInfo("Europe/Moscow")
    now_msk = dt.datetime.now(tz=_MSK)
    current_hhmm = now_msk.strftime("%H:%M")   # e.g. "10:00"
    current_hh00 = now_msk.strftime("%H:00")   # normalised to :00
    today = now_msk.date()

    # Workers who already have a report for today (by report_date, not submission timestamp)
    submitted_today_ids = set(
        EmployeeReport.objects.filter(report_date=today).values_list("user_id", flat=True)
    )

    sent = 0
    failed = 0
    notified_worker_ids: set = set()

    # ── Per-template reminders (uses notification_times) ──────────────────────
    for tmpl in ReportTemplate.objects.prefetch_related("assigned_users").filter(
        assigned_users__isnull=False
    ).distinct():
        times = tmpl.notification_times or []
        # Normalise stored values ("10:00", "10", "10:30") → "HH:MM"
        normalised = []
        for t in times:
            t = str(t).strip()
            if ":" not in t:
                t = t.zfill(2) + ":00"
            normalised.append(t[:5])

        if current_hhmm not in normalised and current_hh00 not in normalised:
            continue

        tmpl_name = tmpl.name or "отчёт"
        text = (
            f"⏰ <b>Напоминание об отчёте</b>\n\n"
            f"Не забудьте сдать отчёт: <b>{tmpl_name}</b>\n\n"
            f"Нажмите «📝 Подать отчёт» в меню."
        )

        for worker in tmpl.assigned_users.filter(
            role=UserRole.WORKER,
            status=UserStatus.ACTIVE,
            is_blocked_bot=False,
        ).exclude(id__in=submitted_today_ids):
            ok = _send_message_sync(worker.telegram_id, text)
            notified_worker_ids.add(worker.pk)
            sent += 1 if ok else 0
            failed += 0 if ok else 1

    # ── Generic reminders for workers without template assignment ─────────────
    _DEFAULT_TIMES = {"10:00", "15:00", "23:00"}
    if current_hhmm in _DEFAULT_TIMES or current_hh00 in _DEFAULT_TIMES:
        workers_no_tmpl = User.objects.filter(
            role=UserRole.WORKER,
            status=UserStatus.ACTIVE,
            is_blocked_bot=False,
        ).exclude(id__in=submitted_today_ids).exclude(
            assigned_report_templates__isnull=False
        )
        text_generic = (
            "⏰ <b>Напоминание</b>\n\n"
            "Необходимо подать отчёт, иначе будет начислен штраф за просрочку."
        )
        for worker in workers_no_tmpl:
            if worker.pk in notified_worker_ids:
                continue
            ok = _send_message_sync(worker.telegram_id, text_generic)
            sent += 1 if ok else 0
            failed += 0 if ok else 1

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
            report_date=today,
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


# ── Deadline reminder notifications ───────────────────────────────────────────

def _get_missing_items_for_worker(worker, deadline_date) -> list[str]:
    """Return list of human-readable names of required items not yet submitted."""
    from apps.control.models import EmployeeReport, ReportTemplate
    from apps.users.models import UserRole

    templates = list(
        ReportTemplate.objects.filter(assigned_users=worker).only("pk", "name")
    )

    if templates:
        submitted_template_ids = set(
            EmployeeReport.objects.filter(
                user=worker,
                report_date=deadline_date,
            ).values_list("template_id", flat=True)
        )
        missing = [
            f"📋 {t.name or 'Отчёт'}"
            for t in templates
            if t.pk not in submitted_template_ids
        ]
    else:
        has_any = EmployeeReport.objects.filter(
            user=worker, report_date=deadline_date
        ).exists()
        missing = [] if has_any else ["📋 Отчёт"]

    return missing


def _get_available_balance(worker) -> tuple:
    """Return (balance, available_for_withdrawal) for the worker, fresh from DB."""
    from apps.withdrawals.models import WithdrawalRequest, WithdrawalStatus
    from django.db.models import Sum

    balance = worker.balance or 0
    pending = (
        WithdrawalRequest.objects.filter(
            user=worker, status=WithdrawalStatus.PENDING
        ).aggregate(s=Sum("amount"))["s"] or 0
    )
    available = max(balance - pending, 0)
    return balance, available


def _send_deadline_notification(worker, slot: str, deadline_date, settings) -> None:
    """
    Send (or skip/log-error) deadline notification for a given worker + slot.
    Idempotent: skips if DeadlineNotificationLog row already exists.
    """
    from apps.control.models import DeadlineNotificationLog, NotificationSlot, NotificationStatus
    from django.db import IntegrityError

    # Idempotency: skip if already processed this slot for this user+date
    if DeadlineNotificationLog.objects.filter(
        user=worker, deadline_date=deadline_date, slot=slot
    ).exists():
        return

    missing = _get_missing_items_for_worker(worker, deadline_date)

    if not missing:
        # Data already submitted — log as skipped
        try:
            DeadlineNotificationLog.objects.create(
                user=worker,
                deadline_date=deadline_date,
                slot=slot,
                status=NotificationStatus.SKIPPED,
                telegram_id=worker.telegram_id,
                missing_items=[],
                balance_snapshot=None,
                available_snapshot=None,
                penalty_snapshot=None,
            )
        except IntegrityError:
            pass
        return

    balance, available = _get_available_balance(worker)
    penalty = settings.late_report_penalty_amount

    slot_labels = {
        NotificationSlot.H23_00: "⚠️ [GRAMLY CRM] Дедлайн через час!",
        NotificationSlot.H23_30: "⚠️ [GRAMLY CRM] Дедлайн через полчаса!",
        NotificationSlot.H23_45: "⚠️ [GRAMLY CRM] Дедлайн через 15 минут!",
        NotificationSlot.H00_00: "⚠️ [GRAMLY CRM] Дедлайн пропущен!",
    }
    header = slot_labels.get(slot, "⚠️ [GRAMLY CRM] Уведомление")
    missing_lines = "\n".join(f"• {item}" for item in missing)

    text = (
        f"{header}\n\n"
        f"Дата: <b>{deadline_date.strftime('%d.%m.%Y')}</b>\n\n"
        f"Не внесено:\n{missing_lines}\n\n"
        f"Штраф за отчёт: <b>{penalty:.2f} ₽</b>\n\n"
        f"💰 Общий баланс: <b>{balance:.2f} ₽</b>\n"
        f"✅ Доступно для вывода: <b>{available:.2f} ₽</b>"
    )

    ok = _send_message_sync(worker.telegram_id, text)
    error_text = "" if ok else "Ошибка отправки (см. логи)"

    try:
        DeadlineNotificationLog.objects.create(
            user=worker,
            deadline_date=deadline_date,
            slot=slot,
            status=NotificationStatus.SENT if ok else NotificationStatus.ERROR,
            error_text=error_text,
            telegram_id=worker.telegram_id,
            missing_items=missing,
            balance_snapshot=balance,
            available_snapshot=available,
            penalty_snapshot=penalty,
        )
    except IntegrityError:
        pass  # race condition — another process already wrote the row

    if ok:
        logger.info(
            "[deadline] Sent slot=%s user=%s date=%s missing=%s",
            slot, worker.telegram_id, deadline_date, missing,
        )
    else:
        logger.error(
            "[deadline] FAILED slot=%s user=%s date=%s",
            slot, worker.telegram_id, deadline_date,
        )


@shared_task(name="apps.control.tasks.deadline_reminder_task", queue="default")
def deadline_reminder_task(slot: str) -> dict:
    """
    Runs at 23:00, 23:30, 23:45, 00:00 MSK.
    Checks each active worker's submissions and sends deadline reminders.

    slot: one of "23:00", "23:30", "23:45", "00:00"
    """
    import datetime as dt
    from zoneinfo import ZoneInfo
    from apps.users.models import User, UserRole, UserStatus
    from apps.control.models import ControlSettings, NotificationSlot

    _MSK = ZoneInfo("Europe/Moscow")
    now_msk = dt.datetime.now(tz=_MSK)

    # At 00:00 we check YESTERDAY (the day that just ended)
    if slot == NotificationSlot.H00_00:
        deadline_date = (now_msk - dt.timedelta(days=1)).date()
    else:
        deadline_date = now_msk.date()

    settings = ControlSettings.get()

    workers = User.objects.filter(
        role=UserRole.WORKER,
        status=UserStatus.ACTIVE,
        is_blocked_bot=False,
    ).only("pk", "telegram_id", "telegram_username", "balance")

    sent = skipped = errors = 0
    for worker in workers:
        from apps.control.models import DeadlineNotificationLog, NotificationStatus
        _send_deadline_notification(worker, slot, deadline_date, settings)
        # Count result from the log row just created (or existing)
        row = DeadlineNotificationLog.objects.filter(
            user=worker, deadline_date=deadline_date, slot=slot
        ).first()
        if row:
            if row.status == NotificationStatus.SENT:
                sent += 1
            elif row.status == NotificationStatus.SKIPPED:
                skipped += 1
            else:
                errors += 1

    logger.info(
        "[deadline] slot=%s date=%s sent=%d skipped=%d errors=%d",
        slot, deadline_date, sent, skipped, errors,
    )
    return {"slot": slot, "date": str(deadline_date), "sent": sent, "skipped": skipped, "errors": errors}
