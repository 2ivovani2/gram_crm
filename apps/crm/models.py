"""
GRAMLY CRM — core models.

Architecture: workspace-scoped (multi-tenant ready).
Every entity belongs to a Workspace, so the same codebase supports
multiple independent projects without any data leakage.

Roles are workspace-scoped via WorkspaceMembership:
  OWNER        — full access; receives all reports; manages members & plans
  FINANCE      — submits Cash Flow data (income, expenses, screenshots, PP/Privat earnings)
  APPLICATIONS — submits application count + earnings
  VIEWER       — read-only access to dashboard and history

Daily data flow:
  FinanceEntry + ApplicationEntry → DailySummaryReport (auto-generated when both submitted)

Deadline tracking:
  DeadlineMiss — created by Celery task at 00:05 MSK if entries are incomplete
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.common.models import TimeStampedModel


# ─── Roles ────────────────────────────────────────────────────────────────────

class CRMRole(models.TextChoices):
    OWNER        = "owner",        "Главный админ (Владелец)"
    FINANCE      = "finance",      "Финансовый аналитик (Cash Flow)"
    APPLICATIONS = "applications", "Менеджер по заявкам"
    VIEWER       = "viewer",       "Наблюдатель (только просмотр)"


# ─── Workspace ────────────────────────────────────────────────────────────────

class Workspace(TimeStampedModel):
    """
    Top-level tenant. All CRM entities belong to a workspace.
    GRAMLY is workspace #1; new clients get their own workspace.
    """
    name        = models.CharField(max_length=120, verbose_name="Название")
    slug        = models.SlugField(max_length=60, unique=True, verbose_name="Слаг (URL)")
    description = models.TextField(blank=True, verbose_name="Описание")
    is_active   = models.BooleanField(default=True, verbose_name="Активен")
    created_by  = models.ForeignKey(
        "users.User",
        null=True,
        on_delete=models.SET_NULL,
        related_name="owned_workspaces",
        verbose_name="Создатель",
    )

    class Meta:
        verbose_name        = "Рабочее пространство"
        verbose_name_plural = "Рабочие пространства"
        ordering            = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def owner_memberships(self):
        return self.memberships.filter(role=CRMRole.OWNER, is_active=True)


# ─── Membership ───────────────────────────────────────────────────────────────

class WorkspaceMembership(TimeStampedModel):
    """
    Scoped role: one User can have different roles in different Workspaces.
    """
    workspace  = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="Пространство",
    )
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="crm_memberships",
        verbose_name="Пользователь",
    )
    role       = models.CharField(
        max_length=20,
        choices=CRMRole.choices,
        default=CRMRole.VIEWER,
        verbose_name="Роль в CRM",
    )
    is_active  = models.BooleanField(default=True, verbose_name="Активен")
    invited_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="crm_invitations_sent",
        verbose_name="Кто пригласил",
    )
    joined_at  = models.DateTimeField(null=True, blank=True, verbose_name="Дата вступления")

    class Meta:
        verbose_name        = "Участник пространства"
        verbose_name_plural = "Участники пространства"
        unique_together     = [("workspace", "user")]
        ordering            = ["workspace", "role", "user__first_name"]

    def __str__(self) -> str:
        return f"{self.user} → {self.workspace} [{self.get_role_display()}]"

    def is_owner(self) -> bool:
        return self.role == CRMRole.OWNER

    def can_enter_finance(self) -> bool:
        return self.role in (CRMRole.OWNER, CRMRole.FINANCE)

    def can_enter_applications(self) -> bool:
        return self.role in (CRMRole.OWNER, CRMRole.APPLICATIONS)

    def can_manage(self) -> bool:
        return self.role == CRMRole.OWNER


# ─── Weekly Plan ─────────────────────────────────────────────────────────────

class WeeklyPlan(TimeStampedModel):
    """
    Financial plan for a given ISO week (Monday as start).
    Used to compute % completion in daily reports.
    """
    workspace      = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="weekly_plans",
        verbose_name="Пространство",
    )
    week_start     = models.DateField(verbose_name="Начало недели (Пн)")
    pp_plan        = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        verbose_name="План ПП на неделю ($)",
    )
    privat_plan    = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        verbose_name="План Приват на неделю ($)",
    )
    created_by     = models.ForeignKey(
        "users.User",
        null=True,
        on_delete=models.SET_NULL,
        related_name="crm_weekly_plans_created",
        verbose_name="Создал",
    )

    class Meta:
        verbose_name        = "Недельный план"
        verbose_name_plural = "Недельные планы"
        unique_together     = [("workspace", "week_start")]
        ordering            = ["-week_start"]

    def __str__(self) -> str:
        return f"{self.workspace} / неделя с {self.week_start}"


# ─── Finance Entry ────────────────────────────────────────────────────────────

def _kb_upload_path(instance, filename: str) -> str:
    ext  = os.path.splitext(filename)[1].lower()
    name = uuid.uuid4().hex
    return f"crm/{instance.workspace_id}/screenshots/{instance.date}/{name}{ext}"


class FinanceEntry(TimeStampedModel):
    """
    Daily Cash Flow data. Submitted by the FINANCE role admin.
    One record per workspace per date (unique_together).
    """
    workspace       = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="finance_entries",
        verbose_name="Пространство",
    )
    date            = models.DateField(verbose_name="Дата", db_index=True)
    submitted_by    = models.ForeignKey(
        "users.User",
        null=True,
        on_delete=models.SET_NULL,
        related_name="crm_finance_entries",
        verbose_name="Внёс",
    )
    # ── Financial data ────────────────────────────────────────────────────────
    income          = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        verbose_name="Сумма поступлений ($)",
    )
    expenses        = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        verbose_name="Сумма расходов / выплат ($)",
    )
    kb_screenshot   = models.FileField(
        upload_to=_kb_upload_path,
        null=True, blank=True,
        verbose_name="Скрин с КБ (файл)",
    )
    pp_earnings     = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        verbose_name="Заработок с ПП за день ($)",
    )
    privat_earnings = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        verbose_name="Заработок с Привата за день ($)",
    )
    kb_balance      = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        verbose_name="Баланс КБ ($)",
        help_text="Текущий баланс на счёте КБ (в долларах)",
    )
    notes           = models.TextField(blank=True, verbose_name="Примечания")
    last_edited_at  = models.DateTimeField(null=True, blank=True, verbose_name="Последнее редактирование")

    class Meta:
        verbose_name        = "Финансовая запись"
        verbose_name_plural = "Финансовые записи"
        unique_together     = [("workspace", "date")]
        ordering            = ["-date"]

    def __str__(self) -> str:
        return f"{self.workspace} / {self.date} (финансы)"

    @property
    def balance(self) -> Decimal:
        """Cash flow balance: income - expenses."""
        return self.income - self.expenses


# ─── Application Entry ────────────────────────────────────────────────────────

class ApplicationEntry(TimeStampedModel):
    """
    Daily applications data. Submitted by the APPLICATIONS role admin.
    One record per workspace per date (unique_together).
    """
    workspace             = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="application_entries",
        verbose_name="Пространство",
    )
    date                  = models.DateField(verbose_name="Дата", db_index=True)
    submitted_by          = models.ForeignKey(
        "users.User",
        null=True,
        on_delete=models.SET_NULL,
        related_name="crm_application_entries",
        verbose_name="Внёс",
    )
    applications_count    = models.PositiveIntegerField(
        default=0,
        verbose_name="Количество заявок за день",
    )
    applications_earnings = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        verbose_name="Заработок с заявок за день ($)",
    )
    notes                 = models.TextField(blank=True, verbose_name="Примечания")
    last_edited_at        = models.DateTimeField(null=True, blank=True, verbose_name="Последнее редактирование")

    class Meta:
        verbose_name        = "Запись по заявкам"
        verbose_name_plural = "Записи по заявкам"
        unique_together     = [("workspace", "date")]
        ordering            = ["-date"]

    def __str__(self) -> str:
        return f"{self.workspace} / {self.date} (заявки)"


# ─── Daily Summary Report ─────────────────────────────────────────────────────

class DailySummaryReport(TimeStampedModel):
    """
    Auto-generated snapshot after both FinanceEntry and ApplicationEntry
    are submitted for the same workspace+date.

    Stores a pre-formatted report_text (plain text with emoji) for
    quick display without re-computation.
    """
    workspace               = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="daily_reports",
        verbose_name="Пространство",
    )
    date                    = models.DateField(verbose_name="Дата", db_index=True)
    finance_entry           = models.OneToOneField(
        FinanceEntry,
        null=True,
        on_delete=models.SET_NULL,
        related_name="summary_report",
        verbose_name="Финансовая запись",
    )
    application_entry       = models.OneToOneField(
        ApplicationEntry,
        null=True,
        on_delete=models.SET_NULL,
        related_name="summary_report",
        verbose_name="Запись по заявкам",
    )
    # ── Snapshot fields (denormalized for history stability) ─────────────────
    pp_earnings             = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    privat_earnings         = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    pp_plan_pct             = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        verbose_name="% выполнения плана ПП",
    )
    privat_plan_pct         = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        verbose_name="% выполнения плана Привата",
    )
    applications_count      = models.PositiveIntegerField(default=0)
    applications_earnings   = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cash_flow_income        = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cash_flow_expenses      = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cash_flow_balance       = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    kb_balance_snapshot     = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        verbose_name="Баланс КБ ($) — снимок",
    )
    # ── Pre-formatted report ─────────────────────────────────────────────────
    report_text             = models.TextField(blank=True, verbose_name="Текст отчёта (plain)")
    generated_at            = models.DateTimeField(auto_now_add=True, verbose_name="Сгенерирован")
    generated_by            = models.ForeignKey(
        "users.User",
        null=True,
        on_delete=models.SET_NULL,
        related_name="crm_reports_generated",
        verbose_name="Сгенерировал",
    )
    # Telegram notifications sent to workspace owners
    telegram_sent           = models.BooleanField(default=False, verbose_name="Отправлен в Telegram")

    class Meta:
        verbose_name        = "Сводный дневной отчёт"
        verbose_name_plural = "Сводные дневные отчёты"
        unique_together     = [("workspace", "date")]
        ordering            = ["-date"]

    def __str__(self) -> str:
        return f"Отчёт {self.workspace} / {self.date}"


# ─── Deadline Miss ────────────────────────────────────────────────────────────

class DeadlineMiss(TimeStampedModel):
    """
    Recorded at 00:05 MSK if at least one entry block is missing for that day.
    Tracks exactly which block was missing so accountability is clear.
    """
    workspace             = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="deadline_misses",
        verbose_name="Пространство",
    )
    date                  = models.DateField(verbose_name="Дата пропуска", db_index=True)
    finance_missing       = models.BooleanField(default=False, verbose_name="Финансы не внесены")
    applications_missing  = models.BooleanField(default=False, verbose_name="Заявки не внесены")
    notified_at           = models.DateTimeField(null=True, blank=True, verbose_name="Уведомление отправлено")

    class Meta:
        verbose_name        = "Пропуск дедлайна"
        verbose_name_plural = "Пропуски дедлайна"
        unique_together     = [("workspace", "date")]
        ordering            = ["-date"]

    def __str__(self) -> str:
        parts = []
        if self.finance_missing:
            parts.append("финансы")
        if self.applications_missing:
            parts.append("заявки")
        return f"{self.workspace} / {self.date} — не внесено: {', '.join(parts)}"

    @property
    def description(self) -> str:
        parts = []
        if self.finance_missing:
            parts.append("Финансы")
        if self.applications_missing:
            parts.append("Заявки")
        return ", ".join(parts) if parts else "—"
