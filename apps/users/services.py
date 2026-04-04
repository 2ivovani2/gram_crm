from __future__ import annotations
import logging
from typing import Optional
from django.db.models import Q
from django.utils import timezone
from .models import User, UserRole, UserStatus

logger = logging.getLogger(__name__)


class UserService:

    @staticmethod
    def get_or_create_from_telegram(
        telegram_id: int,
        first_name: str = "",
        last_name: Optional[str] = None,
        telegram_username: Optional[str] = None,
    ) -> tuple[User, bool]:
        """Get or create User from Telegram update data. Returns (user, created)."""
        user, created = User.objects.get_or_create(
            telegram_id=telegram_id,
            defaults={
                "username": f"tg_{telegram_id}",
                "first_name": first_name or "",
                "last_name": last_name or "",
                "telegram_username": telegram_username,
            },
        )
        if not created:
            changed = {}
            if user.first_name != (first_name or ""):
                changed["first_name"] = first_name or ""
            if user.telegram_username != telegram_username:
                changed["telegram_username"] = telegram_username
            if changed:
                changed["updated_at"] = timezone.now()
                User.objects.filter(pk=user.pk).update(**changed)
                for k, v in changed.items():
                    setattr(user, k, v)

        return user, created

    @staticmethod
    def get_by_telegram_id(telegram_id: int) -> Optional[User]:
        try:
            return User.objects.get(telegram_id=telegram_id)
        except User.DoesNotExist:
            return None

    @staticmethod
    def get_by_pk(pk: int) -> Optional[User]:
        try:
            return User.objects.get(pk=pk)
        except User.DoesNotExist:
            return None

    @staticmethod
    def update_last_activity(user: User) -> None:
        User.objects.filter(pk=user.pk).update(last_activity_at=timezone.now())

    @staticmethod
    def set_status(user: User, status: str) -> User:
        user.status = status
        user.save(update_fields=["status", "updated_at"])
        return user

    @staticmethod
    def set_role(user: User, role: str) -> User:
        user.role = role
        user.save(update_fields=["role", "updated_at"])
        return user

    @staticmethod
    def recalculate_balance(user: User) -> User:
        """
        Balance = attracted_count * personal_rate
                + sum(ref.attracted_count * referral_rate for ref in direct referrals)
        Then subtract total approved withdrawals.
        """
        from decimal import Decimal
        from django.db.models import Sum
        earned = (
            Decimal(user.attracted_count) * user.personal_rate
            + sum(
                Decimal(ref.attracted_count) * user.referral_rate
                for ref in user.referrals.only("attracted_count")
            )
        )
        # Deduct approved withdrawals so balance reflects available funds
        try:
            from apps.withdrawals.models import WithdrawalRequest
            withdrawn = (
                WithdrawalRequest.objects.filter(user=user, status="approved")
                .aggregate(total=Sum("amount"))["total"] or Decimal("0")
            )
        except Exception:
            withdrawn = Decimal("0")

        user.balance = max(Decimal("0"), earned - withdrawn)
        user.save(update_fields=["balance", "updated_at"])
        return user

    @staticmethod
    def set_attracted_count(user: User, count: int) -> User:
        user.attracted_count = count
        user.save(update_fields=["attracted_count", "updated_at"])
        # Recalculate this user's balance
        UserService.recalculate_balance(user)
        # Recalculate the referrer's balance (they earn from this user's attracted_count)
        if user.referred_by_id:
            referrer = User.objects.filter(pk=user.referred_by_id).first()
            if referrer:
                UserService.recalculate_balance(referrer)
        return user

    @staticmethod
    def set_personal_rate(user: User, rate) -> User:
        from decimal import Decimal
        user.personal_rate = Decimal(str(rate))
        user.save(update_fields=["personal_rate", "updated_at"])
        UserService.recalculate_balance(user)
        return user

    @staticmethod
    def set_referral_rate(user: User, rate) -> User:
        from decimal import Decimal
        user.referral_rate = Decimal(str(rate))
        user.save(update_fields=["referral_rate", "updated_at"])
        UserService.recalculate_balance(user)
        return user

    @staticmethod
    def set_work_url(user: User, url: str) -> User:
        user.work_url = url.strip()
        user.save(update_fields=["work_url", "updated_at"])
        return user

    @staticmethod
    def set_referred_by(user: User, referrer: User) -> None:
        User.objects.filter(pk=user.pk).update(referred_by=referrer)
        user.referred_by = referrer

    @staticmethod
    def mark_blocked_bot(user: User) -> None:
        User.objects.filter(pk=user.pk).update(is_blocked_bot=True)

    @staticmethod
    def mark_unblocked_bot(user: User) -> None:
        User.objects.filter(pk=user.pk).update(is_blocked_bot=False)

    @staticmethod
    def get_users_list(page: int = 1, page_size: int = 10) -> tuple[list[User], int]:
        from apps.common.utils import paginate
        qs = User.objects.order_by("-created_at")
        items, total, _ = paginate(qs, page, page_size)
        return items, total

    @staticmethod
    def search_users(query: str) -> list[User]:
        return list(
            User.objects.filter(
                Q(telegram_username__icontains=query)
                | Q(first_name__icontains=query)
                | Q(telegram_id__icontains=query)
            ).order_by("-created_at")[:20]
        )

    @staticmethod
    def get_stats_summary() -> dict:
        total = User.objects.count()
        active = User.objects.filter(status=UserStatus.ACTIVE).count()
        pending = User.objects.filter(status=UserStatus.PENDING).count()
        banned = User.objects.filter(status=UserStatus.BANNED).count()
        admins = User.objects.filter(role=UserRole.ADMIN).count()
        new_today = User.objects.filter(created_at__date=timezone.now().date()).count()
        return {
            "total": total,
            "active": active,
            "pending": pending,
            "banned": banned,
            "admins": admins,
            "workers": total - admins,
            "new_today": new_today,
        }
