from django.db import migrations, models


def preserve_duplicate_reports(apps, schema_editor):
    """Keep every legacy row, but reserve report_date for the canonical latest row."""
    EmployeeReport = apps.get_model("control", "EmployeeReport")
    duplicate_groups = (
        EmployeeReport.objects.exclude(report_date=None)
        .values("user_id", "template_id", "report_date")
        .annotate(row_count=models.Count("id"))
        .filter(row_count__gt=1)
    )
    for group in duplicate_groups.iterator():
        rows = EmployeeReport.objects.filter(
            user_id=group["user_id"],
            template_id=group["template_id"],
            report_date=group["report_date"],
        ).order_by("-submitted_at", "-id")
        canonical = rows.first()
        rows.exclude(pk=canonical.pk).update(report_date=None)


class Migration(migrations.Migration):
    dependencies = [
        ("control", "0015_penalty_template_report_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeereport",
            name="additional_penalty_created_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Время дополнительного штрафа"),
        ),
        migrations.AddField(
            model_name="employeereport",
            name="correction_started_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Начало исправительного периода"),
        ),
        migrations.AddField(
            model_name="employeereport",
            name="initial_penalty_created_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Время первого штрафа за дедлайн"),
        ),
        migrations.AddField(
            model_name="employeereport",
            name="is_late_submission",
            field=models.BooleanField(db_index=True, default=False, verbose_name="Первичная подача после дедлайна"),
        ),
        migrations.AddField(
            model_name="employeereport",
            name="late_window_ends_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Конец окна просроченной подачи"),
        ),
        migrations.AddField(
            model_name="penalty",
            name="source",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Не указан"),
                    ("deadline_missed", "Пропущен первичный дедлайн"),
                    ("correction_expired", "Истёк исправительный период"),
                    ("late_window_expired", "Истекло окно просроченного отчёта"),
                ],
                db_index=True,
                default="",
                max_length=32,
                verbose_name="Основание автоштрафа",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="penalty",
            name="uniq_auto_penalty_user_template_date",
        ),
        migrations.RunPython(preserve_duplicate_reports, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="employeereport",
            constraint=models.UniqueConstraint(
                condition=models.Q(("report_date__isnull", False), ("template__isnull", False)),
                fields=("user", "template", "report_date"),
                name="uniq_report_user_template_date",
            ),
        ),
        migrations.AddConstraint(
            model_name="employeereport",
            constraint=models.UniqueConstraint(
                condition=models.Q(("report_date__isnull", False), ("template__isnull", True)),
                fields=("user", "report_date"),
                name="uniq_generic_report_user_date",
            ),
        ),
        migrations.AddConstraint(
            model_name="penalty",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("report_date__isnull", False),
                    ("source__gt", ""),
                    ("template__isnull", False),
                    ("type", "auto"),
                ),
                fields=("user", "template", "report_date", "source"),
                name="uniq_auto_penalty_user_template_date_source",
            ),
        ),
    ]
