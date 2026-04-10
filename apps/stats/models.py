import datetime
from decimal import Decimal
from django.db import models
from django.utils import timezone


class UserDailyStats(models.Model):
    """Per-user daily task/work metrics. One row per user per day."""

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="daily_stats")
    date = models.DateField(db_index=True)

    tasks_submitted = models.PositiveIntegerField(default=0)
    tasks_completed = models.PositiveIntegerField(default=0)
    tasks_rejected = models.PositiveIntegerField(default=0)

    earned = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        unique_together = [["user", "date"]]
        ordering = ["-date"]
        verbose_name = "Статистика пользователя"
        verbose_name_plural = "Статистика пользователей"

    @property
    def completion_rate(self) -> float:
        if self.tasks_submitted == 0:
            return 0.0
        return round(self.tasks_completed / self.tasks_submitted * 100, 1)


class SystemStats(models.Model):
    """Aggregated system-wide daily snapshot."""

    date = models.DateField(unique=True)
    total_users = models.PositiveIntegerField(default=0)
    active_users = models.PositiveIntegerField(default=0)
    new_users = models.PositiveIntegerField(default=0)
    total_tasks = models.PositiveIntegerField(default=0)
    total_broadcasts = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-date"]
        verbose_name = "Системная статистика"
        verbose_name_plural = "Системная статистика"


class RateConfig(models.Model):
    """
    Singleton: configurable rate split for daily client contracts.

    worker_share  — fraction of client_rate that goes to workers  (e.g. 0.25 = 25 %)
    referral_share — fraction of client_rate that goes to curators (e.g. 0.1389 ≈ 13.89 %)
    our_profit    = client_rate * (1 - worker_share - referral_share)
    """

    worker_share = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0.2500"),
        verbose_name="Доля работника (0–1)",
        help_text="Например 0.25 = 25 % от ставки клиента",
    )
    referral_share = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0.1389"),
        verbose_name="Доля реферала (0–1)",
        help_text="Например 0.1389 ≈ 13.89 % от ставки клиента",
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        verbose_name = "Конфигурация ставок"
        verbose_name_plural = "Конфигурация ставок"

    def __str__(self) -> str:
        return f"Ставки: работник {self.worker_share*100:.2f}%, реферал {self.referral_share*100:.2f}%"

    @classmethod
    def get(cls) -> "RateConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def compute(self, client_rate: Decimal) -> dict:
        """Return dict with worker_rate, referral_rate, our_profit for given client_rate."""
        worker_rate = (client_rate * self.worker_share).quantize(Decimal("0.01"))
        referral_rate = (client_rate * self.referral_share).quantize(Decimal("0.01"))
        our_profit = (client_rate - worker_rate - referral_rate).quantize(Decimal("0.01"))
        return {
            "worker_rate": worker_rate,
            "referral_rate": referral_rate,
            "our_profit": our_profit,
        }


class DailyReport(models.Model):
    """
    Admin-submitted daily client data. One report per day (unique date).
    Triggers daily broadcast to all active workers + curators.
    """

    date = models.DateField(
        unique=True, db_index=True, default=timezone.localdate,
        verbose_name="Дата",
    )
    link = models.URLField(max_length=500, blank=True, verbose_name="Ссылка на пост/канал")
    client_nick = models.CharField(max_length=255, blank=True, verbose_name="Ник клиента")

    client_rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0"),
        verbose_name="Ставка клиента (руб./чел.)",
    )
    total_applications = models.PositiveIntegerField(
        default=0, verbose_name="Заявок за день (шт.)",
    )

    # Auto-computed from RateConfig at time of submission
    worker_rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0"),
        verbose_name="Ставка работника (руб./чел.)",
    )
    referral_rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0"),
        verbose_name="Ставка реферала (руб./чел.)",
    )
    our_profit = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0"),
        verbose_name="Наша прибыль (руб./чел.)",
    )

    broadcast_sent = models.BooleanField(
        default=False, verbose_name="Рассылка отправлена",
        help_text="Флаг защиты от повторной отправки",
    )
    created_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="daily_reports", verbose_name="Внёс",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Дневной отчёт"
        verbose_name_plural = "Дневные отчёты"
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"Отчёт {self.date}: {self.client_nick or '—'} @ {self.client_rate} ₽"

    @property
    def total_worker_payout(self) -> Decimal:
        return (self.worker_rate * self.total_applications).quantize(Decimal("0.01"))

    @property
    def total_referral_payout(self) -> Decimal:
        return (self.referral_rate * self.total_applications).quantize(Decimal("0.01"))

    @property
    def total_our_profit(self) -> Decimal:
        return (self.our_profit * self.total_applications).quantize(Decimal("0.01"))


class MissedDay(models.Model):
    """
    Tracks calendar days where no DailyReport was submitted within the
    control window (23:00–01:00 МСК).

    Created automatically by check_missing_daily_report_task at 01:00–01:59 МСК
    when no report exists for the previous calendar day.

    filled_at / filled_by are populated when admin later submits data backdated
    to this date (via DailyReportService.create_report).
    """

    date = models.DateField(unique=True, db_index=True, verbose_name="Дата пропуска")
    detected_at = models.DateTimeField(auto_now_add=True, verbose_name="Зафиксировано в")
    filled_at = models.DateTimeField(null=True, blank=True, verbose_name="Заполнено позднее")
    filled_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="filled_missed_days", verbose_name="Кем заполнено",
    )

    class Meta:
        ordering = ["-date"]
        verbose_name = "Пропущенный день"
        verbose_name_plural = "Пропущенные дни"

    def __str__(self) -> str:
        if self.filled_at:
            return f"Пропуск {self.date} — заполнен {self.filled_at.strftime('%d.%m %H:%M')}"
        return f"Пропуск {self.date} — не заполнен"

    @property
    def is_filled(self) -> bool:
        return self.filled_at is not None
