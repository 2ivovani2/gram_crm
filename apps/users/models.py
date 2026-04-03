from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class UserRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    WORKER = "worker", "Worker"
    # FUTURE: MODERATOR = "moderator", "Moderator"
    # FUTURE: SUPPORT = "support", "Support"


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
    attracted_count = models.PositiveIntegerField(default=0, help_text="Количество привлечённых людей, выставляется администратором вручную")

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

    # ── Role helpers ──────────────────────────────────────────────────────────

    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

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
