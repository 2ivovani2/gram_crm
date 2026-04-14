from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class WorkLink(models.Model):
    """
    History of work URLs and their attracted_count for a user.

    Financial model:
      personal_earned(user) = SUM(link.attracted_count for ALL WorkLinks of user) × personal_rate
      referral_earned(user) = SUM(
          SUM(ref_link.attracted_count for ALL WorkLinks of ref) × referral_rate
          for ref in direct_referrals(user)
      )
      balance = personal_earned + referral_earned − approved_withdrawals

    Rules:
      - Exactly ONE active WorkLink per user at any time (is_active=True).
      - Replacing a link deactivates the old one (attracted_count frozen),
        creates a new one with attracted_count=0.
      - Historical attracted_count is NEVER zeroed — old earnings persist.
    """
    user = models.ForeignKey(
        "User",
        on_delete=models.CASCADE,
        related_name="work_links",
    )
    url = models.URLField(max_length=500, blank=True)
    attracted_count = models.PositiveIntegerField(
        default=0,
        help_text="Число привлечённых по этой ссылке (замораживается при деактивации)",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, help_text="Причина замены / примечание")

    class Meta:
        verbose_name = "Рабочая ссылка"
        verbose_name_plural = "Рабочие ссылки"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        status = "активна" if self.is_active else "архив"
        return f"WorkLink #{self.pk} ({status}) → {self.user_id} | {self.attracted_count} чел."


class UserRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    CURATOR = "curator", "Curator"
    WORKER = "worker", "Worker"


class UserStatus(models.TextChoices):
    PENDING = "pending", "Pending"      # registered, not yet activated
    ACTIVE = "active", "Active"         # fully operational
    INACTIVE = "inactive", "Inactive"   # manually disabled by admin
    BANNED = "banned", "Banned"         # hard ban


class User(AbstractUser):
    """
    Custom user model. Telegram ID is the primary identity.
    Django's username is auto-generated and not user-facing.
    """

    # Override AbstractUser fields we don't use as primary identity
    username = models.CharField(max_length=150, unique=True, null=True, blank=True)
    email = models.EmailField(blank=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)

    # ── Telegram identity ─────────────────────────────────────────────────────
    telegram_id = models.BigIntegerField(unique=True, db_index=True)
    telegram_username = models.CharField(max_length=150, null=True, blank=True, db_index=True)

    # ── Role & Status ─────────────────────────────────────────────────────────
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.WORKER,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=UserStatus.choices,
        default=UserStatus.PENDING,
        db_index=True,
    )

    # ── Activation ────────────────────────────────────────────────────────────
    is_activated = models.BooleanField(default=False)
    activated_at = models.DateTimeField(null=True, blank=True)

    # ── Referral ─────────────────────────────────────────────────────────────
    referred_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="referrals",
    )

    # ── Worker metrics ────────────────────────────────────────────────────────
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    work_url = models.URLField(max_length=500, blank=True, help_text="Рабочая ссылка, выставляется администратором")
    attracted_count = models.PositiveIntegerField(default=0, help_text="Количество привлечённых подписчиков (прямые), выставляется администратором вручную")

    # ── Rates (per subscriber, in RUB) ────────────────────────────────────────
    personal_rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Ставка за прямых подписчиков (руб. за человека), выставляется администратором",
    )
    referral_rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Ставка за подписчиков рефералов (руб. за человека), выставляется администратором",
    )

    # ── Bot interaction ───────────────────────────────────────────────────────
    is_blocked_bot = models.BooleanField(default=False)  # set when TelegramForbiddenError

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "telegram_id"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"User #{self.telegram_id} [{self.role}|{self.status}]"

    @property
    def display_name(self) -> str:
        if self.first_name:
            return self.first_name
        if self.telegram_username:
            return f"@{self.telegram_username}"
        return str(self.telegram_id)

    @property
    def referral_count(self) -> int:
        return self.referrals.count()

    @property
    def total_attracted(self) -> int:
        """Sum of attracted_count across ALL WorkLinks (active + archived)."""
        from django.db.models import Sum
        result = self.work_links.aggregate(total=Sum("attracted_count"))["total"]
        return result or 0

    @property
    def active_work_link(self) -> "WorkLink | None":
        return self.work_links.filter(is_active=True).first()

    def compute_personal_earned(self) -> "Decimal":
        from decimal import Decimal
        return (Decimal(self.total_attracted) * self.personal_rate).quantize(Decimal("0.01"))

    def compute_referral_earned(self) -> "Decimal":
        from decimal import Decimal
        total = Decimal("0")
        for ref in self.referrals.only("id"):
            total += Decimal(ref.total_attracted) * self.referral_rate
        return total.quantize(Decimal("0.01"))

    def compute_withdrawn(self) -> "Decimal":
        from decimal import Decimal
        from django.db.models import Sum
        try:
            from apps.withdrawals.models import WithdrawalRequest
            result = (
                WithdrawalRequest.objects.filter(user=self, status="approved")
                .aggregate(total=Sum("amount"))["total"]
            )
            return (result or Decimal("0")).quantize(Decimal("0.01"))
        except Exception:
            return Decimal("0")

    def compute_balance(self) -> "Decimal":
        from decimal import Decimal
        earned = self.compute_personal_earned() + self.compute_referral_earned()
        return max(Decimal("0"), earned - self.compute_withdrawn())

    # ── Role helpers ──────────────────────────────────────────────────────────

    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    def is_curator(self) -> bool:
        return self.role == UserRole.CURATOR

    def is_worker(self) -> bool:
        return self.role == UserRole.WORKER

    def can_use_bot(self) -> bool:
        return self.status == UserStatus.ACTIVE and not self.is_blocked_bot

    # ── State transitions ────────────────────────────────────────────────────

    def activate(self) -> None:
        self.is_activated = True
        self.status = UserStatus.ACTIVE
        self.activated_at = timezone.now()
        self.save(update_fields=["is_activated", "status", "activated_at", "updated_at"])

    def deactivate(self) -> None:
        self.status = UserStatus.INACTIVE
        self.save(update_fields=["status", "updated_at"])

    def ban(self) -> None:
        self.status = UserStatus.BANNED
        self.save(update_fields=["status", "updated_at"])


# ─── Join Request ──────────────────────────────────────────────────────────────

class JoinRequestStatus(models.TextChoices):
    PENDING = "pending", "На рассмотрении"
    APPROVED = "approved", "Принята"
    REJECTED = "rejected", "Отклонена"


class JoinRequest(models.Model):
    """
    Replaces the invite key system.

    Flow:
      1. New user sends /start → taps "Подать заявку" → JoinRequest created (PENDING).
      2. All admins receive a notification with Approve / Reject inline buttons.
      3. Admin taps Approve → user.activate() + worker notified.
         Admin taps Reject → user stays PENDING + notified with rejection message.

    One request per user at a time (unique_together on user + PENDING status is
    enforced in the service, not at DB level, to allow re-application after rejection).
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="join_requests",
        verbose_name="Пользователь",
    )
    status = models.CharField(
        max_length=20,
        choices=JoinRequestStatus.choices,
        default=JoinRequestStatus.PENDING,
        db_index=True,
    )
    message = models.TextField(
        blank=True,
        verbose_name="Сообщение от кандидата",
        help_text="Опциональный текст, который пользователь отправил вместе с заявкой",
    )
    reviewed_by = models.ForeignKey(
        User,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_join_requests",
        verbose_name="Рассмотрел",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    # Stores [{telegram_id, message_id}] for each admin notification message
    # so we can edit them after a decision is made.
    admin_notifications = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Заявка на вступление"
        verbose_name_plural = "Заявки на вступление"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"JoinRequest #{self.pk} | {self.user} [{self.status}]"

    @property
    def is_pending(self) -> bool:
        return self.status == JoinRequestStatus.PENDING
