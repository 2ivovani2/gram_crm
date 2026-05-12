from django.db import models


class BroadcastStatus(models.TextChoices):
    DRAFT = "draft", "Черновик"
    CONFIRMED = "confirmed", "Подтверждён"
    RUNNING = "running", "Отправляется"
    DONE = "done", "Завершён"
    FAILED = "failed", "Ошибка"


class BroadcastAudience(models.TextChoices):
    ALL = "all", "Все пользователи"
    ACTIVE = "active", "Только активные"
    INVITED = "invited", "Активированные по инвайту"
    # FUTURE: custom segments (by tag, tariff, geo, etc.)


class DeliveryStatus(models.TextChoices):
    SENT = "sent", "Доставлено"
    FAILED = "failed", "Ошибка"
    BLOCKED = "blocked", "Бот заблокирован"


class Broadcast(models.Model):
    title = models.CharField(max_length=255, help_text="Internal label (not sent to users)")
    text = models.TextField(help_text="Message text. Supports HTML parse mode.")
    parse_mode = models.CharField(max_length=10, default="HTML")

    audience = models.CharField(
        max_length=20,
        choices=BroadcastAudience.choices,
        default=BroadcastAudience.ALL,
    )
    status = models.CharField(
        max_length=20,
        choices=BroadcastStatus.choices,
        default=BroadcastStatus.DRAFT,
        db_index=True,
    )

    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_broadcasts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    total_recipients = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)

    celery_task_id = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Рассылка"
        verbose_name_plural = "Рассылки"

    def __str__(self) -> str:
        return f"{self.title} [{self.status}]"

    @property
    def delivery_rate(self) -> float:
        if self.total_recipients == 0:
            return 0.0
        return round(self.sent_count / self.total_recipients * 100, 1)


class BroadcastDeliveryLog(models.Model):
    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, related_name="delivery_logs")
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="broadcast_deliveries")
    status = models.CharField(max_length=20, choices=DeliveryStatus.choices)
    error_message = models.TextField(blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["broadcast", "user"]]
        ordering = ["-sent_at"]
        verbose_name = "Лог доставки"
        verbose_name_plural = "Логи доставки"
