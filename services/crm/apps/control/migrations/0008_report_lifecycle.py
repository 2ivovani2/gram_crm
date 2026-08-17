"""
Migration 0008 — Report lifecycle upgrade.

Schema changes:
  • EmployeeReport: +first_submission_at, +last_submission_at, +deadline_at,
                    +editing_locked_at, +deadline_met
  • ReportStatus:   +UPDATED ("updated")
  • New model:      ModerationHistory

Data backfill (safe, conservative):
  • first_submission_at  = submitted_at  (best approximation)
  • last_submission_at   = submitted_at
  • deadline_at          = report_date + template.deadline_time @ MSK, where available
  • editing_locked_at    = deadline_at + 1h, where deadline_at is set
  • deadline_met         = NULL (cannot determine reliably for old data)
"""
import datetime
from zoneinfo import ZoneInfo

from django.db import migrations, models
import django.db.models.deletion

_MSK = ZoneInfo("Europe/Moscow")


def _calc_deadline_at(report_date, deadline_time):
    """Combine date + time in MSK → UTC-aware datetime."""
    if report_date is None or deadline_time is None:
        return None
    naive = datetime.datetime.combine(report_date, deadline_time)
    return naive.replace(tzinfo=_MSK)


def backfill_lifecycle_fields(apps, schema_editor):
    EmployeeReport = apps.get_model("control", "EmployeeReport")

    batch = []
    for report in (
        EmployeeReport.objects
        .select_related("template")
        .only("pk", "submitted_at", "report_date", "template__deadline_time")
        .iterator(chunk_size=500)
    ):
        report.first_submission_at = report.submitted_at
        report.last_submission_at = report.submitted_at

        # Deadline from template if available
        tmpl = report.template
        deadline_time = tmpl.deadline_time if tmpl else None
        deadline_at = _calc_deadline_at(report.report_date, deadline_time)
        report.deadline_at = deadline_at
        report.editing_locked_at = (
            deadline_at + datetime.timedelta(hours=1) if deadline_at else None
        )
        report.deadline_met = None  # cannot determine reliably from old data

        batch.append(report)
        if len(batch) >= 500:
            EmployeeReport.objects.bulk_update(
                batch,
                ["first_submission_at", "last_submission_at",
                 "deadline_at", "editing_locked_at", "deadline_met"],
            )
            batch.clear()

    if batch:
        EmployeeReport.objects.bulk_update(
            batch,
            ["first_submission_at", "last_submission_at",
             "deadline_at", "editing_locked_at", "deadline_met"],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("control", "0007_alter_deadlinenotificationlog_id"),
    ]

    operations = [
        # ── 1. New fields on EmployeeReport ───────────────────────────────────
        migrations.AddField(
            model_name="employeereport",
            name="first_submission_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="Время первой подачи"
            ),
        ),
        migrations.AddField(
            model_name="employeereport",
            name="last_submission_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="Время последней подачи"
            ),
        ),
        migrations.AddField(
            model_name="employeereport",
            name="deadline_at",
            field=models.DateTimeField(
                blank=True, null=True,
                verbose_name="Дедлайн отчёта",
                help_text="Рассчитан из шаблона в момент создания",
            ),
        ),
        migrations.AddField(
            model_name="employeereport",
            name="editing_locked_at",
            field=models.DateTimeField(
                blank=True, null=True,
                verbose_name="Блокировка редактирования",
                help_text="deadline_at + 1 час",
            ),
        ),
        migrations.AddField(
            model_name="employeereport",
            name="deadline_met",
            field=models.BooleanField(
                blank=True, null=True,
                verbose_name="Дедлайн соблюдён",
            ),
        ),

        # ── 2. ModerationHistory model ─────────────────────────────────────────
        migrations.CreateModel(
            name="ModerationHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("report", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="history",
                    to="control.employeereport",
                    verbose_name="Отчёт",
                )),
                ("cycle", models.PositiveSmallIntegerField(
                    default=1, verbose_name="Цикл модерации"
                )),
                ("action", models.CharField(
                    max_length=20,
                    choices=[
                        ("submit",        "Первичная подача"),
                        ("resubmit",      "Повторная подача"),
                        ("accept",        "Принятие"),
                        ("reject",        "Отклонение"),
                        ("manual_change", "Ручная смена статуса"),
                    ],
                    verbose_name="Действие",
                )),
                ("moderator", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to="users.user",
                    verbose_name="Модератор / автор",
                )),
                ("prev_status", models.CharField(
                    max_length=20, blank=True, verbose_name="Предыдущий статус"
                )),
                ("new_status", models.CharField(
                    max_length=20, verbose_name="Новый статус"
                )),
                ("comment", models.TextField(blank=True, verbose_name="Комментарий")),
                ("created_at", models.DateTimeField(
                    auto_now_add=True, verbose_name="Дата и время"
                )),
            ],
            options={
                "verbose_name": "История модерации",
                "verbose_name_plural": "История модерации",
                "ordering": ["created_at"],
            },
        ),

        # ── 3. Data backfill ───────────────────────────────────────────────────
        migrations.RunPython(backfill_lifecycle_fields, migrations.RunPython.noop),
    ]
