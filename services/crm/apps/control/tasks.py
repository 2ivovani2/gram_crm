"""
Celery tasks for Gramly Control bot:
- send_report_reminders_task      — template-aware reminders (per notification_times)
- process_report_deadlines_task   — minute-by-minute report lifecycle transitions
- legacy deadline tasks           — compatibility aliases for persisted beat rows
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


@shared_task(name="apps.control.tasks.notify_auto_penalty_created_task", queue="default")
def notify_auto_penalty_created_task(penalty_id: int) -> dict:
    """Send a standalone fine notification required by the report lifecycle."""
    from apps.control.models import Penalty, PenaltySource, PenaltyType
    from apps.control.bot.keyboards import CtrlWorkerCB
    from apps.users.models import User, UserRole, UserStatus
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    penalty = Penalty.objects.select_related("user", "template", "report").filter(
        pk=penalty_id,
        type=PenaltyType.AUTO,
    ).first()
    if penalty is None:
        return {"sent": False, "reason": "penalty_not_found"}
    titles = {
        PenaltySource.DEADLINE_MISSED: "Пропущен дедлайн отчёта",
        PenaltySource.CORRECTION_EXPIRED: "Истёк исправительный период",
        PenaltySource.LATE_WINDOW_EXPIRED: "Истекло 24-часовое окно",
    }
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✏️ Оспорить",
            callback_data=CtrlWorkerCB(action=f"dispute_{penalty.pk}").pack(),
        )
    ]])
    text = (
        f"🚨 <b>[GRAMLY CRM] {titles.get(penalty.source, 'Начислен штраф')}</b>\n\n"
        f"Отчёт: {html.escape(penalty.template.name if penalty.template else 'Отчёт')}\n"
        f"Дата отчёта: {penalty.report_date:%d.%m.%Y}\n"
        f"Сумма: <b>{penalty.amount:.2f} ₽</b>\n"
        f"Причина: {html.escape(penalty.reason)}"
    )
    sent = _send_message_sync(penalty.user.telegram_id, text, reply_markup=keyboard)

    admin_text = (
        "📋 <b>Автоштраф создан</b>\n\n"
        f"Сотрудник: @{html.escape(penalty.user.telegram_username or str(penalty.user.telegram_id))}\n"
        f"Отчёт: {html.escape(penalty.template.name if penalty.template else 'Отчёт')}\n"
        f"Дата: {penalty.report_date:%d.%m.%Y}\n"
        f"Сумма: <b>{penalty.amount:.2f} ₽</b>"
    )
    for admin_id in User.objects.filter(
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        is_blocked_bot=False,
    ).values_list("telegram_id", flat=True):
        _send_message_sync(admin_id, admin_text)
    return {"sent": sent, "penalty_id": penalty.pk}


def queue_auto_penalty_notification(penalty_id: int) -> bool:
    try:
        notify_auto_penalty_created_task.delay(penalty_id)
        return True
    except Exception:
        logger.exception("Failed to enqueue auto penalty notification penalty=%s", penalty_id)
        return False


@shared_task(name="apps.control.tasks.notify_report_decision_task", queue="default")
def notify_report_decision_task(report_id: int) -> dict:
    from zoneinfo import ZoneInfo
    from apps.control.models import EmployeeReport, ReportStatus
    from apps.control.bot.keyboards import worker_report_decision_keyboard

    report = EmployeeReport.objects.select_related("user", "template").filter(pk=report_id).first()
    if report is None or report.status not in {ReportStatus.ACCEPTED, ReportStatus.REJECTED}:
        return {"sent": False, "reason": "report_not_decided"}
    accepted = report.status == ReportStatus.ACCEPTED
    reviewed_at = report.reviewed_at or report.updated_at
    reviewed_msk = reviewed_at.astimezone(ZoneInfo("Europe/Moscow"))
    report_date_label = (
        report.report_date.strftime("%d.%m.%Y")
        if report.report_date
        else (report.period_label or "Не указана")
    )
    can_edit = report.can_user_edit()
    if can_edit and report.editing_locked_at:
        lock_msk = report.editing_locked_at.astimezone(ZoneInfo("Europe/Moscow"))
        editing = f"Доступно до {lock_msk:%H:%M}"
    else:
        editing = "Недоступно"
    text = (
        f"{'✅' if accepted else '⚠️'} <b>[GRAMLY CRM] Отчёт "
        f"{'принят' if accepted else 'отклонён'}!</b>\n\n"
        f"ID отчёта: {report.pk}\n"
        f"Дата отчёта: {report_date_label}\n"
        f"Время решения: {reviewed_msk:%H:%M}\n\n"
        "Комментарий проверяющего:\n"
        f"{html.escape(report.review_comment or 'Без комментария')}\n\n"
        f"Редактирование: {editing}"
    )
    sent = _send_message_sync(
        report.user.telegram_id,
        text,
        reply_markup=worker_report_decision_keyboard(report.pk, can_edit),
    )
    return {"sent": sent, "report_id": report.pk}


def queue_report_decision_notification(report_id: int) -> bool:
    try:
        notify_report_decision_task.delay(report_id)
        return True
    except Exception:
        logger.exception("Failed to enqueue report decision notification report=%s", report_id)
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
        submitted_for_template_ids = set(
            EmployeeReport.objects.filter(
                report_date=today,
                template=tmpl,
            ).values_list("user_id", flat=True)
        )

        for worker in tmpl.assigned_users.filter(
            status=UserStatus.ACTIVE,
            is_blocked_bot=False,
        ).exclude(role=UserRole.ADMIN).exclude(id__in=submitted_for_template_ids):
            ok = _send_message_sync(worker.telegram_id, text, reply_markup=kb)
            notified_worker_ids.add(worker.pk)
            sent += 1 if ok else 0
            failed += 0 if ok else 1

    # ── Generic reminders for workers without template assignment ─────────────
    _DEFAULT_TIMES = {"10:00", "15:00", "23:00"}
    if current_hhmm in _DEFAULT_TIMES or current_hh00 in _DEFAULT_TIMES:
        submitted_today_ids = set(
            EmployeeReport.objects.filter(report_date=today).values_list("user_id", flat=True)
        )
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


def _process_report_deadlines(now=None):
    """Evaluate each template obligation using server time; safe to run every minute."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    from django.utils import timezone
    from apps.control.models import EmployeeReport, Penalty, PenaltySource, ReportStatus, ReportTemplate
    from apps.control.services import ReportDeadlineService
    from apps.users.models import UserRole, UserStatus

    now = now or timezone.now()
    now_msk = now.astimezone(ZoneInfo("Europe/Moscow"))
    dates = [now_msk.date(), now_msk.date() - dt.timedelta(days=1)]
    result = {"initial": 0, "correction": 0, "additional": 0}

    templates = ReportTemplate.objects.prefetch_related("assigned_users").filter(
        auto_penalty_amount__gt=0,
        deadline_time__isnull=False,
    )
    for template in templates:
        workers = template.assigned_users.filter(status=UserStatus.ACTIVE).exclude(role=UserRole.ADMIN)
        for worker in workers:
            for report_date in dates:
                deadline_at = dt.datetime.combine(
                    report_date,
                    template.deadline_time,
                    tzinfo=ZoneInfo("Europe/Moscow"),
                )
                if now < deadline_at:
                    continue
                report = EmployeeReport.objects.select_related("user", "template").filter(
                    user=worker,
                    template=template,
                    report_date=report_date,
                ).first()
                if report_date != now_msk.date() and report is None and not Penalty.objects.filter(
                    user=worker,
                    template=template,
                    report_date=report_date,
                    source=PenaltySource.DEADLINE_MISSED,
                ).exists():
                    # Assignment history is not available. Do not back-charge a
                    # newly assigned worker for yesterday; an obligation enters
                    # the 24-hour follow-up only if its first penalty exists.
                    continue
                timely = bool(
                    report
                    and report.first_submission_at
                    and report.first_submission_at <= deadline_at
                )
                if timely:
                    if (
                        report.status == ReportStatus.REJECTED
                        and report.correction_deadline
                        and now >= report.correction_deadline
                    ):
                        _, created = ReportDeadlineService.create_correction_penalty(report)
                        result["correction"] += int(created)
                    continue

                _, created = ReportDeadlineService.create_initial_penalty(
                    worker,
                    template,
                    report_date,
                    report=report,
                )
                result["initial"] += int(created)

                late_end = deadline_at + dt.timedelta(hours=24)
                if now < late_end:
                    continue
                # A version sent before the window closed must wait for the
                # moderator; their response time must never punish the worker.
                if report and report.status == ReportStatus.ACCEPTED:
                    continue
                if (
                    report
                    and report.status in {ReportStatus.ON_MODERATION, ReportStatus.UPDATED, ReportStatus.PENDING}
                    and report.last_submission_at
                    and report.last_submission_at <= late_end
                ):
                    continue
                _, created = ReportDeadlineService.create_additional_penalty(
                    report,
                    worker=worker,
                    template=template,
                    report_date=report_date,
                )
                result["additional"] += int(created)

    logger.info("[control] Report lifecycle processed: %s", result)
    return result


@shared_task(name="apps.control.tasks.process_report_deadlines_task", queue="default")
def process_report_deadlines_task():
    return _process_report_deadlines()


# Compatibility entry points for persisted django-celery-beat rows. They are
# idempotent and delegate to the same lifecycle engine until old rows disappear.
@shared_task(name="apps.control.tasks.check_overdue_reports_task", queue="default")
def check_overdue_reports_task():
    return _process_report_deadlines()


@shared_task(name="apps.control.tasks.check_correction_deadlines_task", queue="default")
def check_correction_deadlines_task():
    return _process_report_deadlines()


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
