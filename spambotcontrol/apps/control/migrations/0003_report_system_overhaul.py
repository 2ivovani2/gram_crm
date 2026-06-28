"""
Migration: comprehensive report system overhaul.
- ReportTemplate: make user nullable, add name/description/format_instructions/
  deadline_time/auto_penalty_amount/notification_times/correction_deadline_hours/
  created_by/assigned_users
- EmployeeReport: add template FK and correction_deadline
"""
import datetime
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("control", "0002_workerinvite"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── ReportTemplate: make user nullable ────────────────────────────────
        migrations.AlterField(
            model_name="reporttemplate",
            name="user",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="report_template",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Сотрудник (устар.)",
            ),
        ),
        # ── ReportTemplate: new identity fields ───────────────────────────────
        migrations.AddField(
            model_name="reporttemplate",
            name="name",
            field=models.CharField(blank=True, default="", max_length=200, verbose_name="Название"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="reporttemplate",
            name="description",
            field=models.TextField(blank=True, verbose_name="Описание"),
        ),
        migrations.AddField(
            model_name="reporttemplate",
            name="format_instructions",
            field=models.TextField(blank=True, verbose_name="Инструкции по формату"),
        ),
        # ── ReportTemplate: schedule & deadline ───────────────────────────────
        migrations.AddField(
            model_name="reporttemplate",
            name="deadline_time",
            field=models.TimeField(
                blank=True,
                default=datetime.time(23, 0),
                null=True,
                verbose_name="Дедлайн (время)",
            ),
        ),
        migrations.AddField(
            model_name="reporttemplate",
            name="notification_times",
            field=models.JSONField(
                default=list,
                verbose_name="Времена напоминаний",
            ),
        ),
        migrations.AddField(
            model_name="reporttemplate",
            name="correction_deadline_hours",
            field=models.PositiveSmallIntegerField(
                default=24,
                verbose_name="Срок исправления (часов)",
            ),
        ),
        migrations.AddField(
            model_name="reporttemplate",
            name="auto_penalty_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                max_digits=12,
                verbose_name="Штраф за просрочку",
            ),
        ),
        # ── ReportTemplate: ownership ─────────────────────────────────────────
        migrations.AddField(
            model_name="reporttemplate",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_report_templates",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Создал",
            ),
        ),
        # ── ReportTemplate: multi-user assignment M2M ─────────────────────────
        migrations.AddField(
            model_name="reporttemplate",
            name="assigned_users",
            field=models.ManyToManyField(
                blank=True,
                related_name="assigned_report_templates",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Назначенные сотрудники",
            ),
        ),
        # ── EmployeeReport: template FK ───────────────────────────────────────
        migrations.AddField(
            model_name="employeereport",
            name="template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reports",
                to="control.reporttemplate",
                verbose_name="Шаблон",
            ),
        ),
        # ── EmployeeReport: correction deadline ───────────────────────────────
        migrations.AddField(
            model_name="employeereport",
            name="correction_deadline",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Срок исправления",
            ),
        ),
    ]
