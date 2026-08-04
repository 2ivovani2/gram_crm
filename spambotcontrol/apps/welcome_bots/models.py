from __future__ import annotations

import secrets
import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from .crypto import decrypt_token, encrypt_token


class Owner(models.Model):
    telegram_id = models.BigIntegerField(unique=True, db_index=True)
    username = models.CharField(max_length=64, blank=True)
    first_name = models.CharField(max_length=128, blank=True)
    last_name = models.CharField(max_length=128, blank=True)
    guide_completed = models.BooleanField(default=False)
    guide_step = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"@{self.username}" if self.username else str(self.telegram_id)


class ManagedBot(models.Model):
    owner = models.ForeignKey(Owner, on_delete=models.CASCADE, related_name="bots")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    telegram_id = models.BigIntegerField(unique=True, db_index=True)
    username = models.CharField(max_length=64, blank=True)
    display_name = models.CharField(max_length=128)
    token_ciphertext = models.TextField()
    webhook_secret = models.CharField(max_length=64, default=secrets.token_urlsafe)
    path_secret = models.CharField(max_length=64, default=secrets.token_urlsafe)
    is_active = models.BooleanField(default=True, db_index=True)
    webhook_configured = models.BooleanField(default=False)
    welcome_delay_seconds = models.PositiveIntegerField(
        default=0, validators=[MaxValueValidator(30 * 24 * 3600)]
    )
    approval_delay_seconds = models.PositiveIntegerField(
        default=0, validators=[MaxValueValidator(30 * 24 * 3600)]
    )
    auto_approve = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("owner", "is_active"))]

    def __str__(self) -> str:
        return f"@{self.username}" if self.username else self.display_name

    def set_token(self, token: str) -> None:
        self.token_ciphertext = encrypt_token(token)

    def get_token(self) -> str:
        return decrypt_token(self.token_ciphertext)


class Channel(models.Model):
    bot = models.ForeignKey(ManagedBot, on_delete=models.CASCADE, related_name="channels")
    telegram_id = models.BigIntegerField()
    title = models.CharField(max_length=255)
    username = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    can_invite_users = models.BooleanField(default=False)
    connected_at = models.DateTimeField(auto_now_add=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("bot", "telegram_id"), name="welcome_unique_bot_channel")
        ]
        indexes = [models.Index(fields=("bot", "is_active"))]

    def __str__(self) -> str:
        return self.title


class WelcomeMessage(models.Model):
    bot = models.OneToOneField(ManagedBot, on_delete=models.CASCADE, related_name="welcome_message")
    active_version = models.ForeignKey(
        "WelcomeMessageVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="active_for",
    )
    updated_at = models.DateTimeField(auto_now=True)


class WelcomeMessageVersion(models.Model):
    message = models.ForeignKey(WelcomeMessage, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    author_telegram_id = models.BigIntegerField()
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-version",)
        constraints = [
            models.UniqueConstraint(fields=("message", "version"), name="welcome_unique_message_version")
        ]


class WelcomeMedia(models.Model):
    version = models.ForeignKey(WelcomeMessageVersion, on_delete=models.CASCADE, related_name="media")
    position = models.PositiveSmallIntegerField(default=0)
    media_type = models.CharField(max_length=32)
    storage_key = models.CharField(max_length=500)
    original_name = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=128, blank=True)
    size = models.BigIntegerField(default=0)

    class Meta:
        ordering = ("position", "id")


class WelcomeDraft(models.Model):
    """Short-lived collector for Telegram media groups (albums)."""

    bot = models.ForeignKey(ManagedBot, on_delete=models.CASCADE, related_name="welcome_drafts")
    owner = models.ForeignKey(Owner, on_delete=models.CASCADE, related_name="welcome_drafts")
    media_group_id = models.CharField(max_length=128)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("bot", "media_group_id"), name="welcome_unique_media_group_draft")
        ]


class WelcomeDraftMedia(models.Model):
    draft = models.ForeignKey(WelcomeDraft, on_delete=models.CASCADE, related_name="media")
    telegram_message_id = models.BigIntegerField()
    media_type = models.CharField(max_length=32)
    storage_key = models.CharField(max_length=500)
    original_name = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=128, blank=True)
    size = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("telegram_message_id", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("draft", "telegram_message_id"), name="welcome_unique_draft_message"
            )
        ]


class Contact(models.Model):
    class DeliveryStatus(models.TextChoices):
        UNKNOWN = "unknown", "Не проверен"
        LIVE = "live", "Живой"
        DEAD = "dead", "Мёртвый"

    class Gender(models.TextChoices):
        MALE = "male", "Мужчина"
        FEMALE = "female", "Женщина"
        UNKNOWN = "unknown", "Трансформер"

    bot = models.ForeignKey(ManagedBot, on_delete=models.CASCADE, related_name="contacts")
    telegram_id = models.BigIntegerField()
    username = models.CharField(max_length=64, blank=True)
    first_name = models.CharField(max_length=128, blank=True)
    last_name = models.CharField(max_length=128, blank=True)
    language_code = models.CharField(max_length=16, blank=True, default="unknown")
    gender = models.CharField(max_length=16, choices=Gender.choices, default=Gender.UNKNOWN)
    delivery_status = models.CharField(
        max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.UNKNOWN, db_index=True
    )
    bot_started = models.BooleanField(default=False)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    last_delivery_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("bot", "telegram_id"), name="welcome_unique_bot_contact")
        ]
        indexes = [
            models.Index(fields=("bot", "delivery_status")),
            models.Index(fields=("bot", "language_code")),
            models.Index(fields=("bot", "gender")),
        ]


class JoinRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        SCHEDULED = "scheduled", "Запланирована"
        APPROVED = "approved", "Принята"
        CANCELLED = "cancelled", "Отменена"
        FAILED = "failed", "Ошибка"

    bot = models.ForeignKey(ManagedBot, on_delete=models.CASCADE, related_name="join_requests")
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="join_requests")
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="join_requests")
    telegram_update_id = models.BigIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    delay_snapshot_seconds = models.PositiveIntegerField(default=0)
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("bot", "telegram_update_id"), name="welcome_unique_join_update"),
            models.UniqueConstraint(
                fields=("channel", "contact"),
                condition=Q(status__in=("pending", "scheduled")),
                name="welcome_one_open_join_request",
            ),
        ]


class GreetingDelivery(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Запланировано"
        SENT = "sent", "Отправлено"
        FAILED = "failed", "Ошибка"
        CANCELLED = "cancelled", "Отменено"

    bot = models.ForeignKey(ManagedBot, on_delete=models.CASCADE, related_name="deliveries")
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="deliveries")
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="deliveries")
    version = models.ForeignKey(WelcomeMessageVersion, on_delete=models.SET_NULL, null=True)
    event_key = models.CharField(max_length=128)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED, db_index=True)
    delay_snapshot_seconds = models.PositiveIntegerField(default=0)
    due_at = models.DateTimeField(db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("bot", "event_key"), name="welcome_unique_greeting_event")
        ]
        indexes = [models.Index(fields=("status", "due_at"))]


class ProcessedUpdate(models.Model):
    bot = models.ForeignKey(ManagedBot, on_delete=models.CASCADE, related_name="processed_updates")
    update_id = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("bot", "update_id"), name="welcome_unique_processed_update")
        ]


class EventLog(models.Model):
    bot = models.ForeignKey(ManagedBot, on_delete=models.CASCADE, related_name="events", null=True, blank=True)
    owner = models.ForeignKey(Owner, on_delete=models.SET_NULL, related_name="events", null=True, blank=True)
    event_type = models.CharField(max_length=64, db_index=True)
    level = models.CharField(max_length=16, default="info")
    message = models.CharField(max_length=500)
    context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
