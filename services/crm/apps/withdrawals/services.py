from __future__ import annotations
import logging
from decimal import Decimal
from django.utils import timezone
from .models import WithdrawalRequest, WithdrawalStatus

logger = logging.getLogger(__name__)

MIN_WITHDRAWAL_AMOUNT = Decimal("700")


class WithdrawalService:

    @staticmethod
    def create(user, amount: Decimal, method: str, details: str) -> WithdrawalRequest:
        """
        Create a withdrawal request.

        Guards:
          - amount ≥ MIN_WITHDRAWAL_AMOUNT
          - amount ≤ user.balance (prevents requesting more than available)
          - no other PENDING request for this user (prevents double-submit race)
        """
        if amount < MIN_WITHDRAWAL_AMOUNT:
            raise ValueError(f"Минимальная сумма вывода {MIN_WITHDRAWAL_AMOUNT:.0f} ₽")

        if amount > user.balance:
            raise ValueError(
                f"Запрошенная сумма {amount:.2f} ₽ превышает доступный баланс {user.balance:.2f} ₽"
            )

        # Block if there is already a pending request — prevents double-pay
        existing_pending = WithdrawalRequest.objects.filter(
            user=user, status=WithdrawalStatus.PENDING
        ).exists()
        if existing_pending:
            raise ValueError(
                "У вас уже есть заявка в обработке. "
                "Дождитесь её рассмотрения перед созданием новой."
            )

        return WithdrawalRequest.objects.create(
            user=user,
            amount=amount,
            method=method,
            details=details,
        )

    @staticmethod
    def save_admin_notifications(withdrawal: WithdrawalRequest, notifications: list[dict]) -> None:
        """Store list of {"telegram_id": int, "message_id": int} for later editing."""
        withdrawal.admin_notifications = notifications
        withdrawal.save(update_fields=["admin_notifications", "updated_at"])

    @staticmethod
    def approve(withdrawal: WithdrawalRequest, admin_user) -> WithdrawalRequest:
        if withdrawal.status != WithdrawalStatus.PENDING:
            raise ValueError("Заявка уже обработана")
        withdrawal.status = WithdrawalStatus.APPROVED
        withdrawal.processed_by = admin_user
        withdrawal.processed_at = timezone.now()
        withdrawal.save(update_fields=["status", "processed_by", "processed_at", "updated_at"])
        # Recalculate balance — withdrawal is now subtracted from earned
        from apps.users.services import UserService
        from apps.users.models import User
        user = User.objects.get(pk=withdrawal.user_id)
        UserService.recalculate_balance(user)
        return withdrawal

    @staticmethod
    def reject(withdrawal: WithdrawalRequest, admin_user) -> WithdrawalRequest:
        if withdrawal.status != WithdrawalStatus.PENDING:
            raise ValueError("Заявка уже обработана")
        withdrawal.status = WithdrawalStatus.REJECTED
        withdrawal.processed_by = admin_user
        withdrawal.processed_at = timezone.now()
        withdrawal.save(update_fields=["status", "processed_by", "processed_at", "updated_at"])
        # No balance change needed — rejected withdrawals are not subtracted from balance
        return withdrawal

    @staticmethod
    def get_pending() -> list[WithdrawalRequest]:
        return list(
            WithdrawalRequest.objects.filter(status=WithdrawalStatus.PENDING)
            .select_related("user")
            .order_by("created_at")
        )

    @staticmethod
    def get_list(page: int = 1) -> tuple[list[WithdrawalRequest], int]:
        from apps.common.utils import paginate
        qs = WithdrawalRequest.objects.select_related("user", "processed_by").order_by("-created_at")
        items, total, _ = paginate(qs, page, page_size=10)
        return items, total

    @staticmethod
    def get_user_history(user) -> list[WithdrawalRequest]:
        """All withdrawals for a user, newest first."""
        return list(
            WithdrawalRequest.objects.filter(user=user)
            .order_by("-created_at")
        )

    @staticmethod
    def get_user_summary(user) -> dict:
        """
        Return withdrawal summary for a user:
          pending_count  — number of pending requests
          pending_amount — total amount in pending requests
          approved_total — total amount approved (already paid out)
          rejected_count — number of rejected requests
        """
        from django.db.models import Sum, Count, Case, When, IntegerField
        qs = WithdrawalRequest.objects.filter(user=user)
        agg = qs.aggregate(
            pending_count=Count(Case(When(status="pending", then=1), output_field=IntegerField())),
            pending_amount=Sum(Case(When(status="pending", then="amount"), output_field=__import__("django.db.models", fromlist=["DecimalField"]).DecimalField())),
            approved_total=Sum(Case(When(status="approved", then="amount"), output_field=__import__("django.db.models", fromlist=["DecimalField"]).DecimalField())),
            rejected_count=Count(Case(When(status="rejected", then=1), output_field=IntegerField())),
        )
        return {
            "pending_count": agg["pending_count"] or 0,
            "pending_amount": agg["pending_amount"] or Decimal("0"),
            "approved_total": agg["approved_total"] or Decimal("0"),
            "rejected_count": agg["rejected_count"] or 0,
        }
