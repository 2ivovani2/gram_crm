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
    """Format date as Russian genitive month string, e.g. '7 июля 2026'.
    Uses a hardcoded table so the result is locale-independent."""
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
        # New: check M2M assignment
        tmpl = ReportTemplate.objects.filter(assigned_users=user).first()
        if tmpl:
            return tmpl
        # Legacy: per-user OneToOne
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
    def submit_report(
        user: User,
        template: Optional["ReportTemplate"] = None,
        text: str = "",
        file_id: str = "",
        file_type: str = "text",
        original_filename: str = "",
    ) -> "EmployeeReport":
        """Create a new report. Status = ON_MODERATION if template, else PENDING (legacy)."""
        from apps.control.models import EmployeeReport, ReportStatus
        status = ReportStatus.ON_MODERATION if template else ReportStatus.PENDING
        report_date = _calc_report_date()
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
        )
        return report

    @staticmethod
    def accept_report(report: "EmployeeReport", admin: User, comment: str = "") -> None:
        from apps.control.models import ReportStatus
        report.status = ReportStatus.ACCEPTED
        report.reviewed_by = admin
        report.reviewed_at = timezone.now()
        report.review_comment = comment
        report.correction_deadline = None
        report.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "review_comment",
            "correction_deadline", "updated_at",
        ])

    @staticmethod
    def reject_report(report: "EmployeeReport", admin: User, comment: str = "") -> None:
        """Reject and set correction deadline based on template settings."""
        from apps.control.models import ReportStatus
        report.status = ReportStatus.REJECTED
        report.reviewed_by = admin
        report.reviewed_at = timezone.now()
        report.review_comment = comment
        # Set correction deadline from template, or default 24 hours
        hours = 24
        if report.template and report.template.correction_deadline_hours:
            hours = report.template.correction_deadline_hours
        report.correction_deadline = timezone.now() + timedelta(hours=hours)
        report.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "review_comment",
            "correction_deadline", "updated_at",
        ])

    @staticmethod
    def resubmit_report(
        original_report: "EmployeeReport",
        text: str = "",
        file_id: str = "",
        file_type: str = "text",
        original_filename: str = "",
    ) -> "EmployeeReport":
        """Create a correction/resubmission of a rejected report."""
        from apps.control.models import EmployeeReport, ReportStatus
        new_report = EmployeeReport.objects.create(
            user=original_report.user,
            template=original_report.template,
            status=ReportStatus.RESUBMITTED,
            text_content=text,
            telegram_file_id=file_id,
            file_type=file_type or "text",
            original_filename=original_filename,
            report_date=original_report.report_date,  # resubmission keeps the same report_date
            period_label=original_report.period_label,
        )
        return new_report

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
        """Mark as OVERDUE all REJECTED reports whose correction_deadline has passed."""
        from apps.control.models import EmployeeReport, ReportStatus
        now = timezone.now()
        qs = EmployeeReport.objects.filter(
            status=ReportStatus.REJECTED,
            correction_deadline__lt=now,
            correction_deadline__isnull=False,
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
            status=PenaltyStatus.PENDING,
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
        return user.compute_personal_earned() + user.compute_referral_earned()

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
