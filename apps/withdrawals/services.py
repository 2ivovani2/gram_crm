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
        if amount < MIN_WITHDRAWAL_AMOUNT:
            raise ValueError(f"Минимальная сумма вывода {MIN_WITHDRAWAL_AMOUNT:.0f} ₽")
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
        # Deduct from balance
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
