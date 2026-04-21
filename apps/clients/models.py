"""
Client management models.

Architecture:
  Client       — a paying client with nick + rate per application
  ClientLink   — one or more links belonging to a client; each is ACTIVE or INACTIVE
  LinkAssignment — which worker is handling which link right now
                   (one active assignment per link at a time)

Financial model per client:
  total_apps   = SUM(assignment.work_link.attracted_count for all assignments of client)
  client_earned = total_apps × client.rate
  worker_payout = SUM(worker.personal_rate × assignment.work_link.attracted_count)
  referral_payout = SUM(referrer.referral_rate × assignment.work_link.attracted_count
                        for workers who have a referrer)
  net_profit   = client_earned − worker_payout − referral_payout

Inactivity:
  LinkAssignment.last_count_updated_at — updated whenever attracted_count changes.
  Task check_worker_inactivity_task fires daily and unassigns workers idle ≥ 3 days.
"""
from django.db import models
from django.utils import timezone


class LinkStatus(models.TextChoices):
    ACTIVE = "active", "Активна"
    INACTIVE = "inactive", "Деактивирована"
    PAUSED = "paused", "Приостановлена"


class BotCheckStatus(models.TextChoices):
    UNCHECKED = "unchecked", "Не проверено"
    OK = "ok", "Права подтверждены"
    NOT_ADMIN = "not_admin", "Бот не администратор"
    NO_PERMISSIONS = "no_permissions", "Недостаточно прав"
    NO_ACCESS = "no_access", "Нет доступа к каналу"


class UnassignReason(models.TextChoices):
    INACTIVITY = "inactivity", "Неактивность (3 дня)"
    LINK_DEACTIVATED = "link_deactivated", "Ссылка деактивирована"
    MANUAL = "manual", "Ручное снятие"
    REASSIGNED = "reassigned", "Переназначен вручную"


class Client(models.Model):
    nick = models.CharField(
        max_length=200, unique=True,
        verbose_name="Ник клиента",
    )
    rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name="Ставка ($) за заявку",
        help_text="Сколько клиент платит за одну заявку",
    )
    notes = models.TextField(blank=True, verbose_name="Примечания")

    # ── Auto-mode: Telegram invite link generation ────────────────────────────
    # When auto_mode=True, the bot generates a unique invite link per worker
    # instead of using the manual URL. Requires bot to be admin in the channel.
    channel_id = models.BigIntegerField(
        null=True, blank=True,
        verbose_name="Telegram Chat ID",
        help_text="ID канала/группы (число, напр. -1001234567890). Нужен для авто-режима.",
    )
    channel_username = models.CharField(
        max_length=255, blank=True,
        verbose_name="@username канала",
        help_text="Для отображения (заполняется автоматически при проверке прав)",
    )
    auto_mode = models.BooleanField(
        default=False,
        verbose_name="Авто-режим",
        help_text="Если включён — бот генерирует уникальные invite links для воркеров",
    )
    bot_check_status = models.CharField(
        max_length=20,
        choices=BotCheckStatus.choices,
        default=BotCheckStatus.UNCHECKED,
        verbose_name="Статус проверки бота",
    )
    bot_check_detail = models.TextField(
        blank=True,
        verbose_name="Детали проверки",
        help_text="Человекочитаемое объяснение последней проверки прав бота",
    )
    bot_check_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Дата последней проверки",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.nick} ({self.rate}$/заявка)"

    @property
    def active_links(self):
        return self.links.filter(status=LinkStatus.ACTIVE)

    @property
    def total_applications(self) -> int:
        from django.db.models import Sum
        result = (
            LinkAssignment.objects
            .filter(client_link__client=self, work_link__isnull=False)
            .aggregate(total=Sum("work_link__attracted_count"))["total"]
        )
        return result or 0

    @property
    def client_earned(self):
        from decimal import Decimal
        return (Decimal(self.total_applications) * self.rate).quantize(Decimal("0.01"))


class ClientLink(models.Model):
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="links",
        verbose_name="Клиент",
    )
    url = models.URLField(max_length=500, verbose_name="URL ссылки")
    status = models.CharField(
        max_length=20,
        choices=LinkStatus.choices,
        default=LinkStatus.ACTIVE,
        db_index=True,
        verbose_name="Статус",
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivation_note = models.CharField(max_length=500, blank=True, verbose_name="Причина деактивации")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ссылка клиента"
        verbose_name_plural = "Ссылки клиентов"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.client.nick} | {self.url[:60]} [{self.status}]"

    @property
    def active_assignment(self):
        return self.assignments.filter(is_active=True).first()

    @property
    def total_applications(self) -> int:
        from django.db.models import Sum
        result = (
            self.assignments
            .filter(work_link__isnull=False)
            .aggregate(total=Sum("work_link__attracted_count"))["total"]
        )
        return result or 0

    def deactivate(self, note: str = "") -> None:
        self.status = LinkStatus.INACTIVE
        self.deactivated_at = timezone.now()
        self.deactivation_note = note
        self.save(update_fields=["status", "deactivated_at", "deactivation_note"])


class LinkAssignment(models.Model):
    """One worker → one link. At most one active assignment per link."""
    client_link = models.ForeignKey(
        ClientLink,
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name="Ссылка клиента",
    )
    worker = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="link_assignments",
        verbose_name="Воркер",
    )
    # WorkLink created when this assignment started (for earnings history)
    work_link = models.OneToOneField(
        "users.WorkLink",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="assignment",
        verbose_name="Рабочая ссылка воркера",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    assigned_at = models.DateTimeField(auto_now_add=True)
    unassigned_at = models.DateTimeField(null=True, blank=True)
    unassign_reason = models.CharField(
        max_length=30,
        choices=UnassignReason.choices,
        blank=True,
    )
    # Updated whenever admin increments attracted_count for this worker
    last_count_updated_at = models.DateTimeField(null=True, blank=True,
        help_text="Время последнего обновления attracted_count. Используется для проверки неактивности.")

    class Meta:
        verbose_name = "Назначение ссылки"
        verbose_name_plural = "Назначения ссылок"
        ordering = ["-assigned_at"]

    def __str__(self) -> str:
        status = "активно" if self.is_active else "снято"
        return f"{self.worker} → {self.client_link} [{status}]"

    @property
    def applications(self) -> int:
        if self.work_link_id:
            return self.work_link.attracted_count
        return 0
