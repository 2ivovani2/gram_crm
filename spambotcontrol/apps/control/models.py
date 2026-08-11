"""
Gramly Control — HR management models.

Handles employee reports, penalties, KPI, and withdrawal tracking
for the "Грамли Контроль" Telegram bot.
"""
from decimal import Decimal
from datetime import time as dt_time

from django.db import models
from django.utils import timezone


# ── Report system ──────────────────────────────────────────────────────────────

class ReportStatus(models.TextChoices):
    NOT_SUBMITTED  = "not_submitted",  "Не подан"
    PENDING        = "pending",        "Ожидает проверки"   # legacy
    ON_MODERATION  = "on_moderation",  "На модерации"
    ACCEPTED       = "accepted",       "Принят"
    REJECTED       = "rejected",       "Отклонён"
    REVISION       = "revision",       "На доработке"       # legacy
    OVERDUE        = "overdue",        "Просрочен"
    RESUBMITTED    = "resubmitted",    "Повторно отправлен"  # legacy alias
    UPDATED        = "updated",        "Обновлён"


# Statuses that count as "awaiting review" in admin panels
REPORT_MODERATION_STATUSES = {
    ReportStatus.PENDING,
    ReportStatus.ON_MODERATION,
    ReportStatus.RESUBMITTED,
    ReportStatus.UPDATED,
}

# Statuses that block withdrawal
REPORT_BLOCKING_STATUSES = {
    ReportStatus.PENDING,
    ReportStatus.ON_MODERATION,
    ReportStatus.RESUBMITTED,
    ReportStatus.UPDATED,
}

# Statuses in which the user can still edit (before editing_locked_at)
REPORT_EDITABLE_STATUSES = {
    ReportStatus.REJECTED,
}


class ReportTemplate(models.Model):
    """Named report template — admin creates, assigns to multiple workers."""

    # Legacy: per-worker OneToOne template (kept nullable for compat)
    user = models.OneToOneField(
        "users.User",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="report_template",
        verbose_name="Сотрудник (устар.)",
    )

    # Template identity
    name = models.CharField(max_length=200, blank=True, verbose_name="Название")
    description = models.TextField(blank=True, verbose_name="Описание")

    # Template body — prefer format_instructions; content kept for legacy
    content = models.TextField(blank=True, verbose_name="Шаблон (устар.)")
    format_instructions = models.TextField(blank=True, verbose_name="Инструкции по формату")

    # Schedule & deadline
    deadline_time = models.TimeField(
        null=True, blank=True,
        default=dt_time(23, 0),
        verbose_name="Дедлайн (время)",
    )
    notification_times = models.JSONField(
        default=list,
        verbose_name="Времена напоминаний",
        help_text='Список строк "HH:MM", до 3 штук',
    )
    correction_deadline_hours = models.PositiveSmallIntegerField(
        default=24,
        verbose_name="Срок исправления (часов)",
        help_text="Сколько часов даётся на повторную подачу после отклонения",
    )

    # Penalty for overdue / missed correction
    auto_penalty_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0"),
        verbose_name="Штраф за просрочку",
    )

    # Multi-user assignment
    assigned_users = models.ManyToManyField(
        "users.User",
        blank=True,
        related_name="assigned_report_templates",
        verbose_name="Назначенные сотрудники",
    )

    # Ownership
    created_by = models.ForeignKey(
        "users.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="created_report_templates",
        verbose_name="Создал",
    )
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
        ordering = ["name"]

    def __str__(self) -> str:
        if self.name:
            return self.name
        if self.user_id:
            return f"Шаблон для {self.user}"
        return f"Шаблон #{self.pk}"

    @property
    def instructions(self) -> str:
        """Current format instructions (prefer new field, fall back to legacy content)."""
        return self.format_instructions or self.content


class EmployeeReport(models.Model):
    """Report submitted by an employee."""
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="reports",
        verbose_name="Сотрудник",
    )
    # Link to template (null for legacy reports submitted without a template)
    template = models.ForeignKey(
        ReportTemplate,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="reports",
        verbose_name="Шаблон",
    )
    status = models.CharField(
        max_length=20,
        choices=ReportStatus.choices,
        default=ReportStatus.ON_MODERATION,
        db_index=True,
        verbose_name="Статус",
    )

    # Content: either text or a Telegram file
    text_content = models.TextField(blank=True, verbose_name="Текст отчёта")
    telegram_file_id = models.CharField(
        max_length=255, blank=True,
        verbose_name="Telegram file_id",
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

    # Correction deadline (set when report is rejected)
    correction_deadline = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Срок исправления",
    )

    # Period label for display (derived from report_date at submission)
    period_label = models.CharField(
        max_length=100, blank=True,
        verbose_name="Период",
    )

    # Explicit report date — which calendar day this report is FOR.
    # Differs from submitted_at.date() when a worker submits after midnight
    # to cover the previous day's deadline.
    report_date = models.DateField(
        null=True,
        db_index=True,
        verbose_name="Отчётная дата",
        help_text="За какой день этот отчёт. Может отличаться от даты подачи при сдаче после 00:00.",
    )

    # Lifecycle timestamps
    first_submission_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Время первой подачи",
    )
    last_submission_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Время последней подачи",
    )

    # Deadline calculated from template.deadline_time + report_date at submission time.
    # Stored here so changes to the template do not affect existing reports.
    deadline_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Дедлайн отчёта",
        help_text="Рассчитан из шаблона в момент создания; не изменяется при смене дедлайна шаблона",
    )
    # Initial editing cutoff is deadline_at + 1 hour.  A rejection replaces it
    # with the configured correction deadline.
    editing_locked_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Блокировка редактирования",
        help_text="Текущий крайний срок редактирования или исправления",
    )

    # Deadline compliance, computed when the report is accepted.
    # True  = first submission was before deadline AND last submission before editing_locked_at.
    # False = missed deadline or last version submitted after editing_locked_at.
    # None  = not yet determined (report not accepted).
    deadline_met = models.BooleanField(
        null=True, blank=True,
        verbose_name="Дедлайн соблюдён",
    )

    submitted_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания записи")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Отчёт сотрудника"
        verbose_name_plural = "Отчёты сотрудников"
        ordering = ["-submitted_at"]

    def __str__(self) -> str:
        return f"Отчёт #{self.pk} {self.user} [{self.get_status_display()}]"

    @property
    def is_blocking_withdrawal(self) -> bool:
        return self.status in REPORT_BLOCKING_STATUSES

    def can_user_edit(self) -> bool:
        """True if the user is still allowed to edit/resubmit this report."""
        if self.status not in REPORT_EDITABLE_STATUSES:
            return False
        if self.editing_locked_at is None:
            return True
        from django.utils import timezone
        return timezone.now() < self.editing_locked_at


class ModerationHistory(models.Model):
    """Immutable audit trail of every moderation action on a report."""

    class Action(models.TextChoices):
        SUBMIT        = "submit",        "Первичная подача"
        RESUBMIT      = "resubmit",      "Повторная подача"
        ACCEPT        = "accept",        "Принятие"
        REJECT        = "reject",        "Отклонение"
        MANUAL_CHANGE = "manual_change", "Ручная смена статуса"

    report = models.ForeignKey(
        EmployeeReport,
        on_delete=models.CASCADE,
        related_name="history",
        verbose_name="Отчёт",
    )
    cycle = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="Цикл модерации",
        help_text="Номер цикла подача → решение (инкрементируется при каждой повторной подаче)",
    )
    action = models.CharField(
        max_length=20,
        choices=Action.choices,
        verbose_name="Действие",
    )
    moderator = models.ForeignKey(
        "users.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Модератор / автор",
    )
    prev_status = models.CharField(max_length=20, blank=True, verbose_name="Предыдущий статус")
    new_status  = models.CharField(max_length=20, verbose_name="Новый статус")
    comment     = models.TextField(blank=True, verbose_name="Комментарий")
    created_at  = models.DateTimeField(auto_now_add=True, verbose_name="Дата и время")

    class Meta:
        verbose_name        = "История модерации"
        verbose_name_plural = "История модерации"
        ordering            = ["created_at"]

    def __str__(self) -> str:
        return f"#{self.report_id} {self.action} cycle={self.cycle} @ {self.created_at:%d.%m.%Y %H:%M}"


# ── Penalty system ─────────────────────────────────────────────────────────────

class PenaltyType(models.TextChoices):
    AUTO   = "auto",   "Автоматический (просрочка)"
    MANUAL = "manual", "Ручной"


class PenaltyStatus(models.TextChoices):
    CREATED   = "created",   "Создан"
    PENDING   = "pending",   "Активен"
    ACCEPTED  = "accepted",  "Подтверждён"
    REJECTED  = "rejected",  "Отменён"
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
        verbose_name="Назначил",
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
    )
    report_deadline_hour = models.PositiveSmallIntegerField(
        default=23,
        verbose_name="Час дедлайна отчёта (МСК)",
    )
    daily_rate_hour = models.PositiveSmallIntegerField(
        default=20,
        verbose_name="Час начисления ежедневной ставки (МСК, 0–23)",
    )
    min_withdrawal_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=1000,
        verbose_name="Минимальная сумма вывода (₽)",
    )
    withdrawal_processors = models.ManyToManyField(
        "users.User",
        blank=True,
        related_name="as_withdrawal_processor",
        verbose_name="Дополнительные обработчики выводов",
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


# ── Deadline notification log ─────────────────────────────────────────────────

class NotificationSlot(models.TextChoices):
    H23_00 = "23:00", "За час (23:00)"
    H23_30 = "23:30", "За 30 мин (23:30)"
    H23_45 = "23:45", "За 15 мин (23:45)"
    H00_00 = "00:00", "Пропущен (00:00)"


class NotificationStatus(models.TextChoices):
    SENT    = "sent",    "Доставлено"
    SKIPPED = "skipped", "Пропущено (данные внесены)"
    ERROR   = "error",   "Ошибка отправки"


class DeadlineNotificationLog(models.Model):
    """One row per (user × deadline_date × slot). Idempotent — created once."""
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="deadline_notifications",
        verbose_name="Пользователь",
    )
    deadline_date = models.DateField(verbose_name="Дата дедлайна")
    slot          = models.CharField(
        max_length=5,
        choices=NotificationSlot.choices,
        verbose_name="Слот",
    )
    status        = models.CharField(
        max_length=10,
        choices=NotificationStatus.choices,
        verbose_name="Статус",
    )
    error_text    = models.TextField(blank=True, default="", verbose_name="Ошибка")
    telegram_id   = models.BigIntegerField(verbose_name="Telegram ID")
    missing_items = models.JSONField(default=list, verbose_name="Что не внесено")
    balance_snapshot       = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        verbose_name="Баланс на момент отправки",
    )
    available_snapshot     = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        verbose_name="Доступно к выводу",
    )
    penalty_snapshot       = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        verbose_name="Штраф",
    )
    attempted_at  = models.DateTimeField(auto_now_add=True, verbose_name="Время попытки")

    class Meta:
        verbose_name        = "Лог уведомления о дедлайне"
        verbose_name_plural = "Лог уведомлений о дедлайне"
        ordering            = ["-attempted_at"]
        unique_together     = [("user", "deadline_date", "slot")]

    def __str__(self) -> str:
        return f"{self.user} | {self.deadline_date} {self.slot} | {self.status}"


# ── Worker invite ─────────────────────────────────────────────────────────────

class InviteStatus(models.TextChoices):
    PENDING  = "pending",  "Ожидает ответа"
    ACCEPTED = "accepted", "Принято"
    DECLINED = "declined", "Отклонено"


class WorkerInvite(models.Model):
    """Admin-initiated invite sent via bot to a user to join as a worker."""
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="worker_invites",
        verbose_name="Приглашённый",
    )
    invited_by = models.ForeignKey(
        "users.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_worker_invites",
        verbose_name="Кто пригласил",
    )
    status = models.CharField(
        max_length=20,
        choices=InviteStatus.choices,
        default=InviteStatus.PENDING,
        db_index=True,
    )
    bot_message_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Приглашение сотрудника"
        verbose_name_plural = "Приглашения сотрудников"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"WorkerInvite #{self.pk} → {self.user_id} [{self.status}]"
