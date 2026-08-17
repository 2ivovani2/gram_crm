from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.common.models import TimeStampedModel


class TechnicalState(models.TextChoices):
    READY = "ready", "Готово"
    ATTENTION = "attention", "Требует внимания"
    CRITICAL = "critical", "Критическая проблема"
    MISSING = "missing", "Отсутствует"


class OwnerRank(models.TextChoices):
    S = "S", "S"
    A = "A", "A"
    B = "B", "B"
    C = "C", "C"
    D = "D", "D"


class OwnerStatus(TimeStampedModel):
    workspace = models.ForeignKey("crm.Workspace", on_delete=models.CASCADE, related_name="owner_statuses")
    name = models.CharField(max_length=80)
    color = models.CharField(
        max_length=7,
        default="#596623",
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Укажите HEX-цвет вида #596623")],
    )
    emoji = models.CharField(max_length=16, blank=True)
    description = models.CharField(max_length=300, blank=True)
    is_system = models.BooleanField(default=False)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("workspace", "name"), name="owners_unique_status_name")
        ]

    def __str__(self):
        return self.name


class TelegramOwner(TimeStampedModel):
    workspace = models.ForeignKey("crm.Workspace", on_delete=models.CASCADE, related_name="telegram_owners")
    phone = models.CharField(max_length=32, blank=True, db_index=True)
    telegram_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    telegram_username = models.CharField(max_length=64, blank=True, db_index=True)
    display_name = models.CharField(max_length=160, blank=True)
    registered_at = models.DateField(null=True, blank=True)
    used_since = models.DateField(default=timezone.localdate)
    responsible = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="responsible_owners"
    )
    status = models.ForeignKey(
        OwnerStatus, null=True, blank=True, on_delete=models.PROTECT, related_name="owners"
    )
    notes = models.TextField(blank=True)
    notes_updated_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="owner_notes_edited"
    )
    notes_updated_at = models.DateTimeField(null=True, blank=True)

    rank = models.CharField(max_length=1, choices=OwnerRank.choices, default=OwnerRank.D, db_index=True)
    health = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(100)], db_index=True
    )
    sim_state = models.CharField(max_length=16, choices=TechnicalState.choices, default=TechnicalState.MISSING)
    session_state = models.CharField(max_length=16, choices=TechnicalState.choices, default=TechnicalState.MISSING)
    twofa_state = models.CharField(max_length=16, choices=TechnicalState.choices, default=TechnicalState.MISSING)
    proxy_state = models.CharField(max_length=16, choices=TechnicalState.choices, default=TechnicalState.MISSING)
    has_premium = models.BooleanField(default=False)
    is_scam = models.BooleanField(default=False)
    is_blocked = models.BooleanField(default=False)

    session_ciphertext = models.TextField(blank=True)
    twofa_ciphertext = models.TextField(blank=True)
    proxy_ciphertext = models.TextField(blank=True)
    sim_ciphertext = models.TextField(blank=True)

    last_login_at = models.DateTimeField(null=True, blank=True)
    last_login_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="owner_logins"
    )
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ("-updated_at", "-id")
        indexes = [
            models.Index(fields=("workspace", "archived_at", "health"), name="owners_feed_idx"),
            models.Index(fields=("workspace", "rank"), name="owners_rank_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "telegram_id"),
                condition=Q(telegram_id__isnull=False),
                name="owners_unique_workspace_telegram_id",
            ),
            models.UniqueConstraint(
                fields=("workspace", "phone"),
                condition=~Q(phone=""),
                name="owners_unique_workspace_phone",
            ),
        ]

    def __str__(self):
        return self.label

    @property
    def label(self):
        if self.display_name:
            return self.display_name
        if self.telegram_username:
            return f"@{self.telegram_username.lstrip('@')}"
        return self.phone or f"Владелец #{self.pk}"

    @property
    def is_archived(self):
        return self.archived_at is not None

    @property
    def channel_count(self):
        annotated = getattr(self, "active_channel_count", None)
        return annotated if annotated is not None else self.channels.filter(is_archived=False).count()

    @property
    def attention_reasons(self):
        reasons = []
        labels = {
            "session_state": "Session",
            "sim_state": "SIM",
            "twofa_state": "2FA",
            "proxy_state": "Proxy",
        }
        for field, label in labels.items():
            state = getattr(self, field)
            if state == TechnicalState.MISSING:
                reasons.append(f"{label} отсутствует")
            elif state == TechnicalState.CRITICAL:
                reasons.append(f"{label}: критическая проблема")
            elif state == TechnicalState.ATTENTION:
                reasons.append(f"{label} требует внимания")
        if self.is_scam:
            reasons.append("Отметка SCAM")
        if self.is_blocked:
            reasons.append("Аккаунт заблокирован")
        if self.health < 60:
            reasons.append(f"Низкий индекс здоровья: {self.health}%")
        count = self.channel_count
        if count > 10:
            reasons.append(f"Высокая нагрузка: {count} каналов")
        return reasons


class OwnerChannel(TimeStampedModel):
    workspace = models.ForeignKey("crm.Workspace", on_delete=models.CASCADE, related_name="owner_channels")
    owner = models.ForeignKey(TelegramOwner, on_delete=models.PROTECT, related_name="channels")
    telegram_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    title = models.CharField(max_length=255)
    username = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=40, default="active")
    is_archived = models.BooleanField(default=False, db_index=True)
    attached_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("title",)
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "telegram_id"),
                condition=Q(telegram_id__isnull=False),
                name="owners_unique_channel_telegram_id",
            )
        ]


class OwnerChannelHistory(models.Model):
    channel = models.ForeignKey(OwnerChannel, on_delete=models.CASCADE, related_name="ownership_history")
    previous_owner = models.ForeignKey(
        TelegramOwner, null=True, blank=True, on_delete=models.SET_NULL, related_name="channels_transferred_from"
    )
    new_owner = models.ForeignKey(
        TelegramOwner, null=True, blank=True, on_delete=models.SET_NULL, related_name="channels_transferred_to"
    )
    actor = models.ForeignKey("users.User", null=True, on_delete=models.SET_NULL)
    actor_role = models.CharField(max_length=80, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")


class OwnerAuditLog(models.Model):
    owner = models.ForeignKey(TelegramOwner, on_delete=models.CASCADE, related_name="activity")
    actor = models.ForeignKey("users.User", null=True, on_delete=models.SET_NULL, related_name="owner_audit_events")
    actor_username = models.CharField(max_length=160, blank=True)
    actor_role = models.CharField(max_length=80, blank=True)
    event_type = models.CharField(max_length=50, db_index=True)
    description = models.CharField(max_length=500)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [models.Index(fields=("owner", "-created_at"), name="owners_audit_owner_idx")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Owner audit entries are immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Owner audit entries are immutable")


class SavedOwnerFilter(TimeStampedModel):
    workspace = models.ForeignKey("crm.Workspace", on_delete=models.CASCADE, related_name="saved_owner_filters")
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="saved_owner_filters")
    name = models.CharField(max_length=100)
    query = models.JSONField(default=dict)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("workspace", "user", "name"), name="owners_unique_saved_filter")
        ]
