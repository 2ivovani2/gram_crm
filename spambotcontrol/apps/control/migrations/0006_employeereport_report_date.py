"""
Add report_date DateField to EmployeeReport.

report_date = which calendar day the report is FOR.
submitted_at = when it was actually sent (unchanged, auto_now_add).

Data migration: populate report_date for all existing rows.
Rule: if submitted in early morning MSK (00:00–05:59), attribute to previous day.
"""
import datetime
from zoneinfo import ZoneInfo

from django.db import migrations, models


_MSK = ZoneInfo("Europe/Moscow")


def _report_date_from_submitted(submitted_at_utc):
    """Determine report_date from a UTC-aware submitted_at datetime."""
    local = submitted_at_utc.astimezone(_MSK)
    if local.hour < 6:
        return (local - datetime.timedelta(days=1)).date()
    return local.date()


def populate_report_date(apps, schema_editor):
    EmployeeReport = apps.get_model("control", "EmployeeReport")
    batch = []
    for report in EmployeeReport.objects.only("pk", "submitted_at").iterator(chunk_size=500):
        report.report_date = _report_date_from_submitted(report.submitted_at)
        batch.append(report)
        if len(batch) >= 500:
            EmployeeReport.objects.bulk_update(batch, ["report_date"])
            batch = []
    if batch:
        EmployeeReport.objects.bulk_update(batch, ["report_date"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("control", "0005_deadlinenotificationlog"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeereport",
            name="report_date",
            field=models.DateField(
                null=True,
                db_index=True,
                verbose_name="Отчётная дата",
                help_text="За какой день этот отчёт. Может отличаться от даты подачи при сдаче после 00:00.",
            ),
        ),
        migrations.RunPython(populate_report_date, reverse_code=noop),
    ]
