from django.db import models


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
    """
    Aggregated system-wide daily snapshot.
    Populated by a nightly Celery task.
    FUTURE: extend with revenue, antifrod hits, etc.
    """

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
