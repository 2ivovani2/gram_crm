from __future__ import annotations
import logging
from decimal import Decimal
from typing import Optional
from django.db.models import Q, Sum
from django.utils import timezone
from .models import User, UserRole, UserStatus, WorkLink

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
        update_fields = ["status", "updated_at"]
        user.status = status
        # Track deactivation timestamp for retention cohort analysis
        if status == UserStatus.INACTIVE and not user.deactivated_at:
            user.deactivated_at = timezone.now()
            update_fields.append("deactivated_at")
        user.save(update_fields=update_fields)
        return user

    @staticmethod
    def set_role(user: User, role: str) -> User:
        user.role = role
        user.save(update_fields=["role", "updated_at"])
        return user

    # ── Balance ───────────────────────────────────────────────────────────────

    @staticmethod
    def recalculate_balance(user: User) -> User:
        """
        Recompute balance from first principles using WorkLink history:

          personal_earned = SUM(link.attracted_count for ALL WorkLinks) × personal_rate
          referral_earned = SUM(
              SUM(ref_link.attracted_count for ALL ref WorkLinks) × referral_rate
              for ref in direct_referrals
          )
          withdrawn       = SUM(approved WithdrawalRequests)
          balance         = max(0, personal_earned + referral_earned − withdrawn)

        Also syncs User.attracted_count and User.work_url to the active WorkLink
        so all existing read-paths stay correct.
        """
        # Personal earned from ALL historical WorkLinks
        total_attracted = (
            WorkLink.objects.filter(user=user)
            .aggregate(total=Sum("attracted_count"))["total"] or 0
        )
        personal_earned = (Decimal(total_attracted) * user.personal_rate).quantize(Decimal("0.01"))

        # Referral earned: for each direct referral, sum their total attracted × our referral_rate
        referral_earned = Decimal("0")
        for ref in user.referrals.only("id"):
            ref_attracted = (
                WorkLink.objects.filter(user=ref)
                .aggregate(total=Sum("attracted_count"))["total"] or 0
            )
            referral_earned += Decimal(ref_attracted) * user.referral_rate
        referral_earned = referral_earned.quantize(Decimal("0.01"))

        # Deduct approved withdrawals
        from apps.withdrawals.models import WithdrawalRequest
        withdrawn = (
            WithdrawalRequest.objects.filter(user=user, status="approved")
            .aggregate(total=Sum("amount"))["total"] or Decimal("0")
        )

        balance = max(Decimal("0"), personal_earned + referral_earned - withdrawn)

        # Sync denormalized cache fields
        active_link = WorkLink.objects.filter(user=user, is_active=True).first()
        update_fields = ["balance", "updated_at"]
        user.balance = balance

        if active_link is not None:
            user.attracted_count = active_link.attracted_count
            user.work_url = active_link.url
            update_fields += ["attracted_count", "work_url"]

        user.save(update_fields=update_fields)
        return user

    @staticmethod
    def get_earnings_breakdown(user: User) -> dict:
        """
        Return full earnings breakdown for display.

          personal_earned   — from all own WorkLinks × personal_rate
          referral_earned   — from all referrals' WorkLinks × referral_rate
          gross_earned      — personal + referral
          withdrawn         — approved withdrawals
          balance           — gross - withdrawn (≥ 0)
          total_attracted   — sum of all own WorkLinks
          active_attracted  — active WorkLink's count
          referrals_total_attracted — sum of all referrals' attracted
        """
        total_attracted = (
            WorkLink.objects.filter(user=user)
            .aggregate(total=Sum("attracted_count"))["total"] or 0
        )
        active_link = WorkLink.objects.filter(user=user, is_active=True).first()
        personal_earned = (Decimal(total_attracted) * user.personal_rate).quantize(Decimal("0.01"))

        referral_earned = Decimal("0")
        referrals_total_attracted = 0
        for ref in user.referrals.only("id"):
            ref_attracted = (
                WorkLink.objects.filter(user=ref)
                .aggregate(total=Sum("attracted_count"))["total"] or 0
            )
            referrals_total_attracted += ref_attracted
            referral_earned += Decimal(ref_attracted) * user.referral_rate
        referral_earned = referral_earned.quantize(Decimal("0.01"))

        from apps.withdrawals.models import WithdrawalRequest
        withdrawn = (
            WithdrawalRequest.objects.filter(user=user, status="approved")
            .aggregate(total=Sum("amount"))["total"] or Decimal("0")
        )

        gross = personal_earned + referral_earned
        balance = max(Decimal("0"), gross - withdrawn)

        return {
            "personal_earned": personal_earned,
            "referral_earned": referral_earned,
            "gross_earned": gross,
            "withdrawn": withdrawn,
            "balance": balance,
            "total_attracted": total_attracted,
            "active_attracted": active_link.attracted_count if active_link else 0,
            "referrals_total_attracted": referrals_total_attracted,
        }

    # ── WorkLink management ───────────────────────────────────────────────────

    @staticmethod
    def get_or_create_active_work_link(user: User) -> WorkLink:
        """Return the active WorkLink, creating one if none exists."""
        link = WorkLink.objects.filter(user=user, is_active=True).first()
        if link is None:
            link = WorkLink.objects.create(
                user=user,
                url=user.work_url or "",
                attracted_count=user.attracted_count or 0,
                is_active=True,
            )
        return link

    @staticmethod
    def set_attracted_count(user: User, count: int) -> User:
        """
        Set attracted_count on the ACTIVE WorkLink.
        Triggers balance recalculation for this user and their referrer.
        Also updates first_activity_at and reached_60_at milestones.
        Does NOT touch archived WorkLinks.
        """
        from django.db.models import Sum
        link = UserService.get_or_create_active_work_link(user)
        WorkLink.objects.filter(pk=link.pk).update(attracted_count=count)

        UserService.recalculate_balance(user)
        # Referrer earns from this user's attracted_count → recalculate them too
        if user.referred_by_id:
            referrer = User.objects.filter(pk=user.referred_by_id).first()
            if referrer:
                UserService.recalculate_balance(referrer)

        # Update milestone timestamps (first_activity_at, reached_60_at)
        total = (
            WorkLink.objects.filter(user=user)
            .aggregate(total=Sum("attracted_count"))["total"] or 0
        )
        from apps.stats.services import update_user_metrics
        update_user_metrics(user.pk, total)

        return User.objects.get(pk=user.pk)

    @staticmethod
    def set_work_url(user: User, url: str, note: str = "") -> User:
        """
        Set URL on the ACTIVE WorkLink without resetting attracted_count.
        Used for minor URL corrections (same link, different format).
        Does NOT create a new WorkLink — use replace_work_link() for that.
        """
        url = url.strip()
        link = UserService.get_or_create_active_work_link(user)
        WorkLink.objects.filter(pk=link.pk).update(url=url)
        user.work_url = url
        user.save(update_fields=["work_url", "updated_at"])
        return user

    @staticmethod
    def replace_work_link(user: User, new_url: str, note: str = "") -> tuple[WorkLink, WorkLink | None]:
        """
        Replace the active work link with a new one:
          1. Deactivate current active WorkLink (freeze its attracted_count)
          2. Create new WorkLink with attracted_count=0
          3. Sync User.work_url and User.attracted_count to new link
          4. Recalculate balance (old earnings preserved via archived link)

        Returns (new_link, old_link_or_None).
        """
        new_url = new_url.strip()
        old_link = WorkLink.objects.filter(user=user, is_active=True).first()

        if old_link:
            WorkLink.objects.filter(pk=old_link.pk).update(
                is_active=False,
                deactivated_at=timezone.now(),
                note=note or "Заменена администратором",
            )
            old_link.refresh_from_db()

        new_link = WorkLink.objects.create(
            user=user,
            url=new_url,
            attracted_count=0,
            is_active=True,
            note=note or "",
        )

        # Sync denormalized fields
        user.work_url = new_url
        user.attracted_count = 0
        user.save(update_fields=["work_url", "attracted_count", "updated_at"])

        # Recalculate: attracted_count on active link is 0, but archived links preserve history
        UserService.recalculate_balance(user)
        if user.referred_by_id:
            referrer = User.objects.filter(pk=user.referred_by_id).first()
            if referrer:
                UserService.recalculate_balance(referrer)

        return new_link, old_link

    @staticmethod
    def get_work_link_history(user: User) -> list[WorkLink]:
        return list(WorkLink.objects.filter(user=user).order_by("-created_at"))

    # ── Rate setters ──────────────────────────────────────────────────────────

    @staticmethod
    def set_personal_rate(user: User, rate) -> User:
        user.personal_rate = Decimal(str(rate))
        user.save(update_fields=["personal_rate", "updated_at"])
        UserService.recalculate_balance(user)
        return User.objects.get(pk=user.pk)

    @staticmethod
    def set_referral_rate(user: User, rate) -> User:
        user.referral_rate = Decimal(str(rate))
        user.save(update_fields=["referral_rate", "updated_at"])
        UserService.recalculate_balance(user)
        return User.objects.get(pk=user.pk)

    # ── Misc ──────────────────────────────────────────────────────────────────

    @staticmethod
    def clear_work_url(user: User) -> None:
        """
        Archive the active WorkLink (freeze count) and leave user without an active link.
        Used when a link is deactivated — worker keeps their earnings but has no active URL.
        """
        old_link = WorkLink.objects.filter(user=user, is_active=True).first()
        if old_link:
            WorkLink.objects.filter(pk=old_link.pk).update(
                is_active=False,
                deactivated_at=timezone.now(),
                note="Ссылка клиента деактивирована",
            )
        user.work_url = ""
        user.attracted_count = 0
        user.save(update_fields=["work_url", "attracted_count", "updated_at"])
        UserService.recalculate_balance(user)

    @staticmethod
    def mark_blocked_bot(user: User) -> None:
        User.objects.filter(pk=user.pk).update(is_blocked_bot=True)

    @staticmethod
    def mark_unblocked_bot(user: User) -> None:
        User.objects.filter(pk=user.pk).update(is_blocked_bot=False)

    @staticmethod
    def set_referred_by(user: User, referrer: User) -> None:
        User.objects.filter(pk=user.pk).update(referred_by=referrer)
        user.referred_by = referrer

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
        curators = User.objects.filter(role=UserRole.CURATOR).count()
        workers = User.objects.filter(role=UserRole.WORKER).count()
        return {
            "total": total,
            "active": active,
            "pending": pending,
            "banned": banned,
            "admins": admins,
            "curators": curators,
            "workers": workers,
            "new_today": new_today,
        }
