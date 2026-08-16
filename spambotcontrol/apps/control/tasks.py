"""
Celery tasks for Gramly Control bot:
- send_report_reminders_task      — template-aware reminders (per notification_times)
- check_overdue_reports_task      — 23:30 МСК — auto-penalty for workers without any report today
- check_correction_deadlines_task — every hour — auto-penalty for expired rejection deadlines
"""
import asyncio
import html
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


def _submit_report_keyboard():
    """Inline keyboard with a single '📝 Сдать отчёт' button."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from apps.control.bot.keyboards import CtrlWorkerCB
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📝 Сдать отчёт",
            callback_data=CtrlWorkerCB(action="submit_report").pack(),
        )
    ]])


def _send_message_sync(telegram_id: int, text: str, reply_markup=None) -> bool:
    """Send a Telegram message synchronously using a fresh bot instance."""
    try:
        from apps.control.bot.invite_handlers import _make_bot

        async def _send():
            bot = _make_bot()
            try:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
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


@shared_task(name="apps.control.tasks.notify_penalty_created_task", queue="default")
def notify_penalty_created_task(penalty_id: int) -> dict:
    """Notify an employee about a manual penalty, regardless of where it was created."""
    from apps.control.models import Penalty, PenaltyType
    from apps.control.bot.keyboards import CtrlWorkerCB
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    penalty = Penalty.objects.select_related("user").filter(
        pk=penalty_id,
        type=PenaltyType.MANUAL,
    ).first()
    if penalty is None:
        return {"sent": False, "reason": "penalty_not_found"}

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✏️ Оспорить",
            callback_data=CtrlWorkerCB(action=f"dispute_{penalty.pk}").pack(),
        )
    ]])
    text = (
        "⚠️ <b>Вам начислен штраф</b>\n\n"
        f"Сумма: <b>{penalty.amount:.2f} ₽</b>\n"
        f"Причина: {html.escape(penalty.reason)}\n\n"
        "Вы можете оспорить штраф, нажав кнопку ниже."
    )
    sent = _send_message_sync(penalty.user.telegram_id, text, reply_markup=keyboard)
    return {"sent": sent, "penalty_id": penalty.pk}


def queue_penalty_notification(penalty_id: int) -> bool:
    """Enqueue without turning a temporary broker outage into a failed admin action."""
    try:
        notify_penalty_created_task.delay(penalty_id)
        return True
    except Exception:
        logger.exception("Failed to enqueue manual penalty notification penalty=%s", penalty_id)
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
            f"Не забудьте сдать отчёт: <b>{tmpl_name}</b>"
        )
        kb = _submit_report_keyboard()

        for worker in tmpl.assigned_users.filter(
            status=UserStatus.ACTIVE,
            is_blocked_bot=False,
        ).exclude(role=UserRole.ADMIN).exclude(id__in=submitted_today_ids):
            ok = _send_message_sync(worker.telegram_id, text, reply_markup=kb)
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
        kb = _submit_report_keyboard()
        for worker in workers_no_tmpl:
            if worker.pk in notified_worker_ids:
                continue
            ok = _send_message_sync(worker.telegram_id, text_generic, reply_markup=kb)
            sent += 1 if ok else 0
            failed += 0 if ok else 1

    logger.info("[control] Reminders sent: %d, failed: %d", sent, failed)
    return {"sent": sent, "failed": failed}


@shared_task(name="apps.control.tasks.check_overdue_reports_task", queue="default")
def check_overdue_reports_task():
    """
    Check every required report independently.

    Assigned templates use their own auto_penalty_amount. Workers without a
    template keep the legacy generic-report behaviour and global penalty amount.
    """
    from apps.users.models import User, UserRole, UserStatus
    from apps.control.models import EmployeeReport, Penalty, PenaltyType, PenaltyStatus, ControlSettings
    from django.utils import timezone
    import datetime as dt

    settings = ControlSettings.get()
    # Task runs at 00:05 MSK — the deadline was yesterday (the day that just ended)
    yesterday = timezone.localdate() - dt.timedelta(days=1)

    created = 0
    admin_ids = list(
        User.objects.filter(role=UserRole.ADMIN, status=UserStatus.ACTIVE, is_blocked_bot=False)
        .values_list("telegram_id", flat=True)
    )

    def notify(penalty, worker, report_label):
        nonlocal created
        created += 1
        worker_text = (
            f"🚨 <b>Начислен штраф за просрочку</b>\n\n"
            f"За неподанный отчёт «{html.escape(report_label)}» "
            f"за {yesterday.strftime('%d.%m.%Y')} начислен штраф "
            f"<b>{penalty.amount:.2f} ₽</b>.\n\n"
            "Для оспаривания обратитесь к администратору."
        )
        _send_message_sync(worker.telegram_id, worker_text)
        admin_text = (
            f"📋 <b>Автоштраф создан</b>\n\n"
            f"Сотрудник: @{html.escape(worker.telegram_username or str(worker.telegram_id))}\n"
            f"Отчёт: {html.escape(report_label)}\n"
            f"Дата: {yesterday.strftime('%d.%m.%Y')}\n"
            f"Сумма: <b>{penalty.amount:.2f} ₽</b>"
        )
        for admin_tg_id in admin_ids:
            _send_message_sync(admin_tg_id, admin_text)

    # Template-aware penalties. A report for another template must not satisfy
    # this assignment, which was the root cause for newly-created templates.
    from apps.control.models import ReportTemplate
    templates = ReportTemplate.objects.prefetch_related("assigned_users").filter(
        auto_penalty_amount__gt=0,
        assigned_users__status=UserStatus.ACTIVE,
    ).distinct()
    assigned_worker_ids = set()
    for template in templates:
        workers = template.assigned_users.filter(
            status=UserStatus.ACTIVE,
        ).exclude(role=UserRole.ADMIN)
        for worker in workers:
            assigned_worker_ids.add(worker.pk)
            submitted = EmployeeReport.objects.filter(
                user=worker,
                template=template,
                report_date=yesterday,
            ).exists()
            if submitted:
                continue
            penalty, was_created = Penalty.objects.get_or_create(
                user=worker,
                template=template,
                report_date=yesterday,
                type=PenaltyType.AUTO,
                defaults={
                    "amount": template.auto_penalty_amount,
                    "reason": (
                        f"Просрочка отчёта «{template.name or 'Отчёт'}» "
                        f"({yesterday.strftime('%d.%m.%Y')})"
                    ),
                    "status": PenaltyStatus.ACCEPTED,
                },
            )
            if was_created:
                notify(penalty, worker, template.name or "Отчёт")

    # Legacy generic report for workers without a template assignment.
    if settings.late_report_penalty_amount > 0:
        submitted_ids = set(
            EmployeeReport.objects.filter(report_date=yesterday).values_list("user_id", flat=True)
        )
        workers = User.objects.filter(
            role=UserRole.WORKER,
            status=UserStatus.ACTIVE,
        ).exclude(id__in=submitted_ids).exclude(id__in=assigned_worker_ids).exclude(
            assigned_report_templates__isnull=False
        )
    else:
        workers = User.objects.none()

    for worker in workers:
        already = Penalty.objects.filter(
            user=worker,
            type=PenaltyType.AUTO,
            reason__startswith=f"Просрочка подачи отчёта ({yesterday.strftime('%-d %B %Y')})",
        ).exists()
        if already:
            continue

        penalty = Penalty.objects.create(
            user=worker,
            type=PenaltyType.AUTO,
            amount=settings.late_report_penalty_amount,
            reason=f"Просрочка подачи отчёта ({yesterday.strftime('%-d %B %Y')})",
            status=PenaltyStatus.ACCEPTED,
        )
        notify(penalty, worker, "Отчёт")

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
                    status=PenaltyStatus.ACCEPTED,
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
    elif worker.role == UserRole.WORKER:
        # Generic fallback only for workers without a specific template
        has_any = EmployeeReport.objects.filter(
            user=worker, report_date=deadline_date
        ).exists()
        missing = [] if has_any else ["📋 Отчёт"]
    else:
        # Non-workers without a template assigned don't have a required report
        missing = []

    return missing


def _get_available_balance(worker) -> tuple:
    """Return (balance, available_for_withdrawal) for the worker, fresh from DB."""
    from apps.withdrawals.models import WithdrawalRequest, WithdrawalStatus
    from apps.control.services import ControlBalanceService
    from django.db.models import Sum

    balance = ControlBalanceService.get_total_balance(worker)
    pending = (
        WithdrawalRequest.objects.filter(
            user=worker, status=WithdrawalStatus.PENDING
        ).aggregate(s=Sum("amount"))["s"] or 0
    )
    available = max(ControlBalanceService.get_available_balance(worker) - pending, 0)
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

    # Only show submit button for non-"missed" slots
    kb = _submit_report_keyboard() if slot != "00:00" else None
    ok = _send_message_sync(worker.telegram_id, text, reply_markup=kb)
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
        status=UserStatus.ACTIVE,
        is_blocked_bot=False,
    ).exclude(role=UserRole.ADMIN)

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


@shared_task(name="apps.control.tasks.accrue_daily_rate_task", queue="default")
def accrue_daily_rate_task(force: bool = False) -> dict:
    """
    Runs every hour. Accrues daily_rate to each eligible active worker
    when the current MSK hour matches ControlSettings.daily_rate_hour.
    Idempotent: skips if already accrued today (daily_rate_last_accrued_date == today).
    """
    import datetime as dt
    from zoneinfo import ZoneInfo
    from decimal import Decimal
    from django.db import transaction
    from apps.users.models import User, UserRole, UserStatus
    from apps.control.models import ControlSettings
    from apps.control.services import ControlBalanceService

    _MSK = ZoneInfo("Europe/Moscow")
    now_msk = dt.datetime.now(tz=_MSK)
    today = now_msk.date()

    settings = ControlSettings.get()
    if not force and now_msk.hour != settings.daily_rate_hour:
        return {"skipped": True, "reason": "not the accrual hour"}

    workers = User.objects.filter(
        status=UserStatus.ACTIVE,
        daily_rate__gt=0,
    ).exclude(
        role=UserRole.ANONYMOUS,
    ).exclude(
        daily_rate_last_accrued_date=today,
    ).values_list("pk", flat=True)

    accrued_count = 0
    notification_sent = 0
    notification_failed = 0
    for worker_id in workers:
        # Lock each user so duplicate/retried tasks cannot credit the same day twice.
        with transaction.atomic():
            worker = User.objects.select_for_update().get(pk=worker_id)
            if (
                worker.status != UserStatus.ACTIVE
                or worker.role == UserRole.ANONYMOUS
                or worker.daily_rate <= 0
                or worker.daily_rate_last_accrued_date == today
            ):
                continue

            amount = worker.daily_rate
            worker.daily_accrued = (
                (worker.daily_accrued or Decimal("0")) + amount
            )
            worker.daily_rate_last_accrued_date = today
            worker.save(update_fields=[
                "daily_accrued",
                "daily_rate_last_accrued_date",
                "updated_at",
            ])

        accrued_count += 1
        available = ControlBalanceService.get_available_balance(worker)

        text = (
            f"💰 <b>Начисление ставки</b>\n\n"
            f"Сегодня вам начислена ежедневная ставка: <b>+{amount:.2f} ₽</b>\n"
            f"Доступный баланс: <b>{available:.2f} ₽</b>"
        )
        if _send_message_sync(worker.telegram_id, text):
            notification_sent += 1
        else:
            notification_failed += 1

    logger.info(
        "[control] Daily rate accrued for %d employees on %s; notifications sent=%d failed=%d",
        accrued_count,
        today,
        notification_sent,
        notification_failed,
    )
    return {
        "date": str(today),
        "accrued": accrued_count,
        "notification_sent": notification_sent,
        "notification_failed": notification_failed,
    }
