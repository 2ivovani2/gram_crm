"""
Gramly Control — HR management models.

Handles employee reports, penalties, KPI, and withdrawal tracking
for the "Грамли Контроль" Telegram bot.
"""
from django.db import models
from django.utils import timezone


# ── Report system ──────────────────────────────────────────────────────────────

class ReportStatus(models.TextChoices):
    NOT_SUBMITTED = "not_submitted", "Не подан"
    PENDING       = "pending",       "Ожидает проверки"
    ACCEPTED      = "accepted",      "Принят"
    REJECTED      = "rejected",      "Отклонён"
    REVISION      = "revision",      "На доработке"
    OVERDUE       = "overdue",       "Просрочен"


class ReportTemplate(models.Model):
    """Individual report template assigned to each employee by admin."""
    user = models.OneToOneField(
        "users.User",
        on_delete=models.CASCADE,
        related_name="report_template",
        verbose_name="Сотрудник",
    )
    content = models.TextField(verbose_name="Шаблон отчёта")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        "users.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_report_templates",
        verbose_name="Обновил",
    )

    class Meta:
        verbose_name = "Шаблон отчёта"
        verbose_name_plural = "Шаблоны отчётов"

    def __str__(self) -> str:
        return f"Шаблон для {self.user}"


class EmployeeReport(models.Model):
    """Report submitted by an employee."""
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="reports",
        verbose_name="Сотрудник",
    )
    status = models.CharField(
        max_length=20,
        choices=ReportStatus.choices,
        default=ReportStatus.PENDING,
        db_index=True,
        verbose_name="Статус",
    )
    # Content: either text or a Telegram file
    text_content = models.TextField(blank=True, verbose_name="Текст отчёта")
    telegram_file_id = models.CharField(
        max_length=255, blank=True,
        verbose_name="Telegram file_id",
        help_text="ID файла в Telegram (если отчёт — документ/фото)",
    )
    file_type = models.CharField(
        max_length=20, blank=True,
        verbose_name="Тип файла",
        help_text="document / photo / text",
    )
    original_filename = models.CharField(max_length=255, blank=True)

    # Admin review
    reviewed_by = models.ForeignKey(
        "users.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_reports",
        verbose_name="Проверил",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True, verbose_name="Комментарий администратора")

    # Deadline tracking
    period_label = models.CharField(
        max_length=100, blank=True,
        verbose_name="Период",
        help_text="Например: '25 мая 2026'",
    )

    submitted_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата подачи")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Отчёт сотрудника"
        verbose_name_plural = "Отчёты сотрудников"
        ordering = ["-submitted_at"]

    def __str__(self) -> str:
        return f"Отчёт #{self.pk} {self.user} [{self.get_status_display()}]"

    @property
    def is_blocking_withdrawal(self) -> bool:
        return self.status == ReportStatus.PENDING


# ── Penalty system ─────────────────────────────────────────────────────────────

class PenaltyType(models.TextChoices):
    AUTO   = "auto",   "Автоматический (просрочка)"
    MANUAL = "manual", "Ручной"


class PenaltyStatus(models.TextChoices):
    CREATED   = "created",   "Создан"
    PENDING   = "pending",   "Ожидает подтверждения"
    ACCEPTED  = "accepted",  "Принят"
    REJECTED  = "rejected",  "Отклонён"
    DISPUTED  = "disputed",  "Оспаривается"
    DELETED   = "deleted",   "Удалён"


class Penalty(models.Model):
    """Fine issued to an employee — either automatic (late report) or manual."""
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="penalties",
        verbose_name="Сотрудник",
    )
    type = models.CharField(
        max_length=10,
        choices=PenaltyType.choices,
        db_index=True,
        verbose_name="Тип",
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        verbose_name="Сумма штрафа",
    )
    reason = models.CharField(max_length=500, verbose_name="Причина")
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    status = models.CharField(
        max_length=20,
        choices=PenaltyStatus.choices,
        default=PenaltyStatus.CREATED,
        db_index=True,
        verbose_name="Статус",
    )

    # Relations
    created_by = models.ForeignKey(
        "users.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="created_penalties",
        verbose_name="Создал",
        help_text="null для автоматических штрафов",
    )
    report = models.ForeignKey(
        EmployeeReport,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="penalties",
        verbose_name="Связанный отчёт",
    )

    # Dispute
    dispute_comment = models.TextField(blank=True, verbose_name="Комментарий при оспаривании")

    # Resolution
    resolved_by = models.ForeignKey(
        "users.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_penalties",
        verbose_name="Принял решение",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    # Notification message ID for bot alerts
    bot_notification_message_id = models.BigIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Штраф"
        verbose_name_plural = "Штрафы"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Штраф #{self.pk} {self.user} {self.amount}₽ [{self.get_status_display()}]"

    def accept(self, admin) -> None:
        self.status = PenaltyStatus.ACCEPTED
        self.resolved_by = admin
        self.resolved_at = timezone.now()
        self.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])

    def reject(self, admin) -> None:
        self.status = PenaltyStatus.REJECTED
        self.resolved_by = admin
        self.resolved_at = timezone.now()
        self.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])

    def delete_soft(self, admin) -> None:
        self.status = PenaltyStatus.DELETED
        self.resolved_by = admin
        self.resolved_at = timezone.now()
        self.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])


# ── KPI system ────────────────────────────────────────────────────────────────

class KPISettings(models.Model):
    """Per-employee KPI rates and additional info."""
    user = models.OneToOneField(
        "users.User",
        on_delete=models.CASCADE,
        related_name="kpi_settings",
        verbose_name="Сотрудник",
    )
    base_rate = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name="Базовая ставка",
    )
    bonus_rate = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name="Ставка премирования",
    )
    penalty_rate = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name="Ставка штрафов",
    )
    other_info = models.TextField(blank=True, verbose_name="Прочие показатели")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        "users.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_kpi_settings",
        verbose_name="Обновил",
    )

    class Meta:
        verbose_name = "Настройки KPI"
        verbose_name_plural = "Настройки KPI"

    def __str__(self) -> str:
        return f"KPI {self.user}"


class KPIDocument(models.Model):
    """Individual KPI .docx document uploaded by admin for each employee."""
    user = models.OneToOneField(
        "users.User",
        on_delete=models.CASCADE,
        related_name="kpi_document",
        verbose_name="Сотрудник",
    )
    file = models.FileField(
        upload_to="kpi_documents/",
        verbose_name="KPI-документ (.docx)",
    )
    original_filename = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        "users.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_kpi_documents",
        verbose_name="Загрузил",
    )

    class Meta:
        verbose_name = "KPI-документ"
        verbose_name_plural = "KPI-документы"

    def __str__(self) -> str:
        return f"KPI-документ {self.user}"


# ── Global settings ────────────────────────────────────────────────────────────

class ControlSettings(models.Model):
    """Singleton — global settings for the control bot."""
    late_report_penalty_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name="Штраф за просрочку отчёта",
        help_text="Сумма штрафа, автоматически начисляемого за неподанный отчёт",
    )
    report_deadline_hour = models.PositiveSmallIntegerField(
        default=23,
        verbose_name="Час дедлайна отчёта (МСК)",
        help_text="Час по московскому времени, после которого отчёт считается просроченным",
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        "users.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Обновил",
    )

    class Meta:
        verbose_name = "Настройки Контроль-бота"
        verbose_name_plural = "Настройки Контроль-бота"

    def __str__(self) -> str:
        return "Настройки Контроль-бота"

    @classmethod
    def get(cls) -> "ControlSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ── Broadcast history ─────────────────────────────────────────────────────────
# Reuses existing apps.broadcasts.Broadcast model — no duplication needed.
