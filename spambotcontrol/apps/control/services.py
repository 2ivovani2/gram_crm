"""
Business logic for the Gramly Control bot.
All methods are sync — wrap with sync_to_async in async bot handlers.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.users.models import User, UserRole

logger = logging.getLogger(__name__)


# ── Report services ────────────────────────────────────────────────────────────

class ReportService:

    @staticmethod
    def get_pending_report(user: User):
        """Return the most recent report in PENDING status, if any."""
        from apps.control.models import EmployeeReport, ReportStatus
        return (
            EmployeeReport.objects.filter(user=user, status=ReportStatus.PENDING)
            .order_by("-submitted_at")
            .first()
        )

    @staticmethod
    def has_blocking_report(user: User) -> bool:
        """True if the worker has a pending report that blocks withdrawal."""
        return ReportService.get_pending_report(user) is not None

    @staticmethod
    def get_template(user: User) -> Optional[str]:
        """Return report template text for the user, or None."""
        from apps.control.models import ReportTemplate
        try:
            return user.report_template.content
        except Exception:
            return None

    @staticmethod
    def submit_report(user: User, text: str = "", file_id: str = "",
                      file_type: str = "text", original_filename: str = "") -> "EmployeeReport":
        """Create a new report and return it."""
        from apps.control.models import EmployeeReport, ReportStatus
        from django.utils import timezone
        report = EmployeeReport.objects.create(
            user=user,
            status=ReportStatus.PENDING,
            text_content=text,
            telegram_file_id=file_id,
            file_type=file_type or "text",
            original_filename=original_filename,
            period_label=timezone.localdate().strftime("%-d %B %Y"),
        )
        return report

    @staticmethod
    def accept_report(report, admin: User, comment: str = "") -> None:
        from apps.control.models import ReportStatus
        report.status = ReportStatus.ACCEPTED
        report.reviewed_by = admin
        report.reviewed_at = timezone.now()
        report.review_comment = comment
        report.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_comment", "updated_at"])

    @staticmethod
    def reject_report(report, admin: User, comment: str = "") -> None:
        from apps.control.models import ReportStatus
        report.status = ReportStatus.REJECTED
        report.reviewed_by = admin
        report.reviewed_at = timezone.now()
        report.review_comment = comment
        report.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_comment", "updated_at"])

    @staticmethod
    def send_to_revision(report, admin: User, comment: str = "") -> None:
        from apps.control.models import ReportStatus
        report.status = ReportStatus.REVISION
        report.reviewed_by = admin
        report.reviewed_at = timezone.now()
        report.review_comment = comment
        report.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_comment", "updated_at"])

    @staticmethod
    def get_all_pending():
        from apps.control.models import EmployeeReport, ReportStatus
        return EmployeeReport.objects.filter(status=ReportStatus.PENDING).select_related("user").order_by("-submitted_at")

    @staticmethod
    def get_reports_for_user(user: User):
        from apps.control.models import EmployeeReport
        return EmployeeReport.objects.filter(user=user).order_by("-submitted_at")[:10]


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
    def create_auto(worker: User, report=None) -> "Penalty":
        from apps.control.models import Penalty, PenaltyType, PenaltyStatus, ControlSettings
        settings = ControlSettings.get()
        return Penalty.objects.create(
            user=worker,
            type=PenaltyType.AUTO,
            amount=settings.late_report_penalty_amount,
            reason="Просрочка подачи отчёта",
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
        ).select_related("user", "created_by").order_by("-created_at")

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
        """Total ever earned (gross, before penalties and withdrawals)."""
        return user.compute_personal_earned() + user.compute_referral_earned()

    @staticmethod
    def get_available_balance(user: User) -> Decimal:
        """Balance available for withdrawal (after penalties and withdrawals)."""
        penalties = PenaltyService.total_accepted_penalty(user)
        gross = ControlBalanceService.get_total_balance(user)
        withdrawn = user.compute_withdrawn()
        available = gross - withdrawn - penalties
        from decimal import Decimal as D
        return max(D("0"), available)


# ── Worker list helper ─────────────────────────────────────────────────────────

def get_all_workers():
    return User.objects.filter(
        role=UserRole.WORKER,
        status="active",
    ).order_by("telegram_username")


def get_all_accountants():
    return User.objects.filter(role=UserRole.ACCOUNTANT, status="active")
