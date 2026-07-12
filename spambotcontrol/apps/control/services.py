"""
Business logic for the Gramly Control bot.
All methods are sync — wrap with sync_to_async in async bot handlers.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Optional, List

from django.db import transaction
from django.utils import timezone

import datetime as _dt
from zoneinfo import ZoneInfo as _ZoneInfo

from apps.users.models import User, UserRole

logger = logging.getLogger(__name__)

_MSK = _ZoneInfo("Europe/Moscow")

_RU_MONTHS_GEN = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _format_period_label(d: _dt.date) -> str:
    """Format date as Russian genitive month string, e.g. '7 июля 2026'."""
    return f"{d.day} {_RU_MONTHS_GEN[d.month]} {d.year}"


def _calc_report_date() -> _dt.date:
    """
    Determine which calendar date a report being submitted NOW belongs to.

    Deadline is midnight (00:00 MSK). Workers who miss it and submit in the
    early morning hours (00:00–05:59) are filing a LATE report for the previous
    day — not an early report for the new day.

    Returns yesterday's date for 00:00–05:59 MSK, today's date otherwise.
    """
    now_msk = timezone.now().astimezone(_MSK)
    if now_msk.hour < 6:
        return (now_msk - _dt.timedelta(days=1)).date()
    return now_msk.date()


def _calc_deadline_at(
    template: Optional["ReportTemplate"],
    report_date: _dt.date,
) -> Optional[_dt.datetime]:
    """Combine report_date + template.deadline_time in MSK → UTC-aware datetime."""
    if template is None or template.deadline_time is None or report_date is None:
        return None
    naive = _dt.datetime.combine(report_date, template.deadline_time)
    return naive.replace(tzinfo=_MSK)


# ── Report services ────────────────────────────────────────────────────────────

class ReportService:

    @staticmethod
    def get_active_templates_for_user(user: User) -> List["ReportTemplate"]:
        """Templates assigned to this worker via M2M (new flow)."""
        from apps.control.models import ReportTemplate
        return list(
            ReportTemplate.objects.filter(assigned_users=user).order_by("name")
        )

    @staticmethod
    def get_template_for_user(user: User) -> Optional["ReportTemplate"]:
        """Return the first template assigned to this user (new M2M or legacy OneToOne)."""
        from apps.control.models import ReportTemplate
        tmpl = ReportTemplate.objects.filter(assigned_users=user).first()
        if tmpl:
            return tmpl
        try:
            return user.report_template
        except Exception:
            return None

    @staticmethod
    def get_template(user: User) -> Optional[str]:
        """Return format instructions for the user's template, or None (legacy compat)."""
        tmpl = ReportService.get_template_for_user(user)
        return tmpl.instructions if tmpl else None

    @staticmethod
    def has_blocking_report(user: User) -> bool:
        """True if the worker has a report in a blocking status."""
        from apps.control.models import EmployeeReport, REPORT_BLOCKING_STATUSES
        return EmployeeReport.objects.filter(
            user=user,
            status__in=REPORT_BLOCKING_STATUSES,
        ).exists()

    @staticmethod
    def get_blocking_report(user: User) -> Optional["EmployeeReport"]:
        """Return the blocking report, if any."""
        from apps.control.models import EmployeeReport, REPORT_BLOCKING_STATUSES
        return (
            EmployeeReport.objects.filter(user=user, status__in=REPORT_BLOCKING_STATUSES)
            .order_by("-submitted_at")
            .first()
        )

    @staticmethod
    def can_moderate(moderator: User, report: "EmployeeReport") -> bool:
        """
        Permission check for moderation actions (accept / reject).

        Rules:
        - ADMIN can moderate all reports except their own.
        - CURATOR can moderate reports of WORKER and ACCOUNTANT only (not own, not other curators).
        - WORKER / ACCOUNTANT cannot moderate anyone.
        """
        if report.user_id == moderator.pk:
            return False
        if moderator.role == UserRole.ADMIN:
            return True
        if moderator.role == UserRole.CURATOR:
            return report.user.role in (UserRole.WORKER, UserRole.ACCOUNTANT)
        return False

    @staticmethod
    @transaction.atomic
    def submit_report(
        user: User,
        template: Optional["ReportTemplate"] = None,
        text: str = "",
        file_id: str = "",
        file_type: str = "text",
        original_filename: str = "",
    ) -> "EmployeeReport":
        """
        Submit (or resubmit) a report.

        Single-record rule: one EmployeeReport per (user, template, report_date).
        • If no existing record → create new (first submission).
        • If existing record is REJECTED and editing is still allowed → update in-place
          (status → UPDATED, last_submission_at refreshed).
        • If existing record is REJECTED but editing is locked → raise ValueError.
        • Any other blocking status → raise ValueError (already pending/accepted).
        """
        from apps.control.models import (
            EmployeeReport, ModerationHistory, ReportStatus,
            REPORT_BLOCKING_STATUSES, REPORT_EDITABLE_STATUSES,
        )

        report_date = _calc_report_date()
        now = timezone.now()

        # Look for an existing report for the same user + template + date
        existing = (
            EmployeeReport.objects.select_for_update()
            .filter(user=user, template=template, report_date=report_date)
            .first()
        )

        if existing is not None:
            if existing.status in REPORT_BLOCKING_STATUSES:
                raise ValueError(
                    f"Отчёт уже находится на проверке (статус: {existing.get_status_display()})."
                )
            if existing.status in REPORT_EDITABLE_STATUSES:
                if not existing.can_user_edit():
                    raise ValueError(
                        "Время на исправление истекло — редактирование заблокировано."
                    )
                # Determine current cycle number from history
                last_cycle = (
                    existing.history.order_by("-cycle").values_list("cycle", flat=True).first()
                    or 1
                )
                new_cycle = last_cycle + 1
                prev_status = existing.status
                existing.status = ReportStatus.UPDATED
                existing.text_content = text
                existing.telegram_file_id = file_id
                existing.file_type = file_type or "text"
                existing.original_filename = original_filename
                existing.last_submission_at = now
                existing.save(update_fields=[
                    "status", "text_content", "telegram_file_id",
                    "file_type", "original_filename", "last_submission_at", "updated_at",
                ])
                ModerationHistory.objects.create(
                    report=existing,
                    cycle=new_cycle,
                    action=ModerationHistory.Action.RESUBMIT,
                    moderator=None,
                    prev_status=prev_status,
                    new_status=ReportStatus.UPDATED,
                )
                return existing
            # Any other terminal status (ACCEPTED, OVERDUE…) — start fresh
            # Fall through to create a new record only when template differs or
            # truly different report_date, but in practice we block duplicate dates.
            raise ValueError(
                f"Отчёт за этот период уже существует (статус: {existing.get_status_display()})."
            )

        # First submission — create a new record
        status = ReportStatus.ON_MODERATION if template else ReportStatus.PENDING
        deadline_at = _calc_deadline_at(template, report_date)
        editing_locked_at = (deadline_at + _dt.timedelta(hours=1)) if deadline_at else None

        report = EmployeeReport.objects.create(
            user=user,
            template=template,
            status=status,
            text_content=text,
            telegram_file_id=file_id,
            file_type=file_type or "text",
            original_filename=original_filename,
            report_date=report_date,
            period_label=_format_period_label(report_date),
            first_submission_at=now,
            last_submission_at=now,
            deadline_at=deadline_at,
            editing_locked_at=editing_locked_at,
        )
        ModerationHistory.objects.create(
            report=report,
            cycle=1,
            action=ModerationHistory.Action.SUBMIT,
            moderator=None,
            prev_status="",
            new_status=status,
        )
        return report

    @staticmethod
    @transaction.atomic
    def accept_report(report: "EmployeeReport", admin: User, comment: str = "") -> None:
        from apps.control.models import ReportStatus, ModerationHistory

        prev_status = report.status
        now = timezone.now()

        # Compute deadline_met
        deadline_met = None
        if report.deadline_at and report.first_submission_at and report.editing_locked_at:
            deadline_met = (
                report.first_submission_at < report.deadline_at
                and report.last_submission_at < report.editing_locked_at
            )

        report.status = ReportStatus.ACCEPTED
        report.reviewed_by = admin
        report.reviewed_at = now
        report.review_comment = comment
        report.correction_deadline = None
        report.deadline_met = deadline_met
        report.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "review_comment",
            "correction_deadline", "deadline_met", "updated_at",
        ])

        cycle = (
            report.history.order_by("-cycle").values_list("cycle", flat=True).first() or 1
        )
        ModerationHistory.objects.create(
            report=report,
            cycle=cycle,
            action=ModerationHistory.Action.ACCEPT,
            moderator=admin,
            prev_status=prev_status,
            new_status=ReportStatus.ACCEPTED,
            comment=comment,
        )

    @staticmethod
    @transaction.atomic
    def reject_report(report: "EmployeeReport", admin: User, comment: str = "") -> None:
        """Reject report. editing_locked_at was set at creation and is not modified here."""
        from apps.control.models import ReportStatus, ModerationHistory

        prev_status = report.status
        now = timezone.now()

        report.status = ReportStatus.REJECTED
        report.reviewed_by = admin
        report.reviewed_at = now
        report.review_comment = comment
        # Keep correction_deadline for legacy display; but editing_locked_at
        # (set at submit time = deadline_at + 1h) is the authoritative lock.
        hours = 24
        if report.template and report.template.correction_deadline_hours:
            hours = report.template.correction_deadline_hours
        report.correction_deadline = now + timedelta(hours=hours)
        report.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "review_comment",
            "correction_deadline", "updated_at",
        ])

        cycle = (
            report.history.order_by("-cycle").values_list("cycle", flat=True).first() or 1
        )
        ModerationHistory.objects.create(
            report=report,
            cycle=cycle,
            action=ModerationHistory.Action.REJECT,
            moderator=admin,
            prev_status=prev_status,
            new_status=ReportStatus.REJECTED,
            comment=comment,
        )

    @staticmethod
    def resubmit_report(
        original_report: "EmployeeReport",
        text: str = "",
        file_id: str = "",
        file_type: str = "text",
        original_filename: str = "",
    ) -> "EmployeeReport":
        """
        Legacy shim — now delegates to submit_report so the single-record invariant
        is preserved. Kept so old call sites don't break.
        """
        return ReportService.submit_report(
            user=original_report.user,
            template=original_report.template,
            text=text,
            file_id=file_id,
            file_type=file_type,
            original_filename=original_filename,
        )

    @staticmethod
    def send_to_revision(report: "EmployeeReport", admin: User, comment: str = "") -> None:
        """Legacy: send to revision (kept for backward compat)."""
        from apps.control.models import ReportStatus
        report.status = ReportStatus.REVISION
        report.reviewed_by = admin
        report.reviewed_at = timezone.now()
        report.review_comment = comment
        report.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_comment", "updated_at"])

    @staticmethod
    def get_all_pending():
        """All reports awaiting review (any admin)."""
        from apps.control.models import EmployeeReport, REPORT_MODERATION_STATUSES
        return (
            EmployeeReport.objects.filter(status__in=REPORT_MODERATION_STATUSES)
            .select_related("user", "template", "template__created_by")
            .order_by("-submitted_at")
        )

    @staticmethod
    def get_pending_for_admin(admin: User):
        """Reports for templates created by this admin (new routing)."""
        from apps.control.models import EmployeeReport, REPORT_MODERATION_STATUSES
        return (
            EmployeeReport.objects.filter(
                status__in=REPORT_MODERATION_STATUSES,
                template__created_by=admin,
            )
            .select_related("user", "template")
            .order_by("-submitted_at")
        )

    @staticmethod
    def get_reports_for_user(user: User):
        from apps.control.models import EmployeeReport
        return EmployeeReport.objects.filter(user=user).order_by("-submitted_at")[:10]

    @staticmethod
    def mark_overdue_correction_deadlines() -> int:
        """Mark as OVERDUE all REJECTED reports whose editing_locked_at has passed."""
        from apps.control.models import EmployeeReport, ReportStatus
        now = timezone.now()
        qs = EmployeeReport.objects.filter(
            status=ReportStatus.REJECTED,
            editing_locked_at__lt=now,
            editing_locked_at__isnull=False,
        )
        count = qs.count()
        qs.update(status=ReportStatus.OVERDUE, updated_at=now)
        return count


# ── Penalty services ───────────────────────────────────────────────────────────

class PenaltyService:

    @staticmethod
    def create_manual(admin: User, worker: User, amount: Decimal,
                      reason: str, comment: str = "") -> "Penalty":
        from apps.control.models import Penalty, PenaltyType, PenaltyStatus
        return Penalty.objects.create(
            user=worker,
            type=PenaltyType.MANUAL,
            amount=amount,
            reason=reason,
            comment=comment,
            status=PenaltyStatus.CREATED,
            created_by=admin,
        )

    @staticmethod
    def create_auto(worker: User, report=None, amount: Optional[Decimal] = None,
                    reason: str = "Просрочка подачи отчёта") -> "Penalty":
        from apps.control.models import Penalty, PenaltyType, PenaltyStatus, ControlSettings
        if amount is None:
            settings = ControlSettings.get()
            amount = settings.late_report_penalty_amount
        return Penalty.objects.create(
            user=worker,
            type=PenaltyType.AUTO,
            amount=amount,
            reason=reason,
            status=PenaltyStatus.ACCEPTED,
            report=report,
        )

    @staticmethod
    def get_active_for_user(user: User):
        from apps.control.models import Penalty, PenaltyStatus
        return Penalty.objects.filter(
            user=user,
        ).exclude(status=PenaltyStatus.DELETED).order_by("-created_at")

    @staticmethod
    def dispute(penalty, dispute_comment: str) -> None:
        from apps.control.models import PenaltyStatus
        penalty.status = PenaltyStatus.DISPUTED
        penalty.dispute_comment = dispute_comment
        penalty.save(update_fields=["status", "dispute_comment", "updated_at"])

    @staticmethod
    def get_pending_penalties():
        from apps.control.models import Penalty, PenaltyStatus
        return Penalty.objects.filter(
            status__in=[PenaltyStatus.PENDING, PenaltyStatus.DISPUTED]
        ).select_related("user", "created_by", "report", "report__template").order_by("-created_at")

    @staticmethod
    def total_accepted_penalty(user: User) -> Decimal:
        from apps.control.models import Penalty, PenaltyStatus
        from django.db.models import Sum
        result = Penalty.objects.filter(
            user=user, status=PenaltyStatus.ACCEPTED
        ).aggregate(total=Sum("amount"))["total"]
        return result or Decimal("0")


# ── Withdrawal services ────────────────────────────────────────────────────────

class ControlWithdrawalService:

    @staticmethod
    def create(user: User, wallet_address: str) -> "WithdrawalRequest":
        from apps.withdrawals.models import WithdrawalRequest, WithdrawalMethod, WithdrawalStatus
        amount = user.compute_balance()
        if amount <= 0:
            raise ValueError("Недостаточно средств для вывода")
        if ReportService.has_blocking_report(user):
            raise ValueError("Вывод заблокирован: отчёт ожидает проверки")
        existing = WithdrawalRequest.objects.filter(
            user=user, status=WithdrawalStatus.PENDING
        ).first()
        if existing:
            raise ValueError("У вас уже есть активная заявка на вывод")
        return WithdrawalRequest.objects.create(
            user=user,
            amount=amount,
            method=WithdrawalMethod.USDT_TRC20,
            details=wallet_address,
            status=WithdrawalStatus.PENDING,
        )

    @staticmethod
    def mark_processing(withdrawal, accountant: User) -> None:
        from apps.withdrawals.models import WithdrawalStatus
        withdrawal.status = WithdrawalStatus.PROCESSING
        withdrawal.processed_by = accountant
        withdrawal.save(update_fields=["status", "processed_by", "updated_at"])

    @staticmethod
    def mark_receipt_sent(withdrawal, accountant: User) -> None:
        from apps.withdrawals.models import WithdrawalStatus
        withdrawal.status = WithdrawalStatus.RECEIPT_SENT
        withdrawal.processed_by = accountant
        withdrawal.processed_at = timezone.now()
        withdrawal.save(update_fields=["status", "processed_by", "processed_at", "updated_at"])

    @staticmethod
    def mark_completed(withdrawal, accountant: User) -> None:
        from apps.withdrawals.models import WithdrawalStatus
        withdrawal.status = WithdrawalStatus.APPROVED
        withdrawal.processed_by = accountant
        withdrawal.processed_at = timezone.now()
        withdrawal.save(update_fields=["status", "processed_by", "processed_at", "updated_at"])

    @staticmethod
    def reject(withdrawal, accountant: User) -> None:
        from apps.withdrawals.models import WithdrawalStatus
        withdrawal.status = WithdrawalStatus.REJECTED
        withdrawal.processed_by = accountant
        withdrawal.processed_at = timezone.now()
        withdrawal.save(update_fields=["status", "processed_by", "processed_at", "updated_at"])

    @staticmethod
    def get_pending_list():
        from apps.withdrawals.models import WithdrawalRequest, WithdrawalStatus
        return WithdrawalRequest.objects.filter(
            status__in=[WithdrawalStatus.PENDING, WithdrawalStatus.PROCESSING, WithdrawalStatus.RECEIPT_SENT]
        ).select_related("user").order_by("created_at")


# ── KPI services ───────────────────────────────────────────────────────────────

class KPIService:

    @staticmethod
    def get_or_create_settings(user: User) -> "KPISettings":
        from apps.control.models import KPISettings
        obj, _ = KPISettings.objects.get_or_create(user=user)
        return obj

    @staticmethod
    def update_settings(user: User, admin: User, base_rate: Decimal,
                        bonus_rate: Decimal, penalty_rate: Decimal,
                        other_info: str = "") -> "KPISettings":
        from apps.control.models import KPISettings
        obj, _ = KPISettings.objects.get_or_create(user=user)
        obj.base_rate = base_rate
        obj.bonus_rate = bonus_rate
        obj.penalty_rate = penalty_rate
        obj.other_info = other_info
        obj.updated_by = admin
        obj.save()
        return obj

    @staticmethod
    def get_document(user: User) -> Optional["KPIDocument"]:
        from apps.control.models import KPIDocument
        try:
            return user.kpi_document
        except Exception:
            return None


# ── Balance calculation for Control bot ──────────────────────────────────────

class ControlBalanceService:

    @staticmethod
    def get_total_balance(user: User) -> Decimal:
        return (
            user.compute_personal_earned()
            + user.compute_referral_earned()
            + (user.daily_accrued or Decimal("0"))
        )

    @staticmethod
    def get_available_balance(user: User) -> Decimal:
        penalties = PenaltyService.total_accepted_penalty(user)
        gross = ControlBalanceService.get_total_balance(user)
        withdrawn = user.compute_withdrawn()
        available = gross - withdrawn - penalties
        return max(Decimal("0"), available)


# ── Worker list helper ─────────────────────────────────────────────────────────

def get_all_workers():
    return User.objects.filter(role=UserRole.WORKER, status="active").order_by("telegram_username")


def get_all_accountants():
    return User.objects.filter(role=UserRole.ACCOUNTANT, status="active")
