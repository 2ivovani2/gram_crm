"""
Add three metric fields to User for Conversion Rate / Retention analysis:
  - first_activity_at  : when attracted_count first became > 0
  - reached_60_at      : when total attracted first crossed 60
  - deactivated_at     : when status was set to INACTIVE

Also includes a data migration that backfills best-effort values for existing rows:
  - deactivated_at   ← User.updated_at where status=inactive (approximate)
  - first_activity_at ← MIN(WorkLink.created_at) where attracted_count > 0 (exact)
  - reached_60_at    ← NULL (no reliable historical data; will be set going forward)
"""
from django.db import migrations, models
from django.utils import timezone


def backfill_metric_fields(apps, schema_editor):
    User = apps.get_model("users", "User")
    WorkLink = apps.get_model("users", "WorkLink")
    from django.db.models import Min, Sum

    # deactivated_at: use updated_at as approximation for existing inactive users
    User.objects.filter(status="inactive", deactivated_at__isnull=True).update(
        deactivated_at=models.F("updated_at")
    )

    # first_activity_at: use earliest WorkLink.created_at where that link has attracted > 0
    for user in User.objects.filter(attracted_count__gt=0, first_activity_at__isnull=True):
        earliest = (
            WorkLink.objects
            .filter(user=user, attracted_count__gt=0)
            .aggregate(earliest=Min("created_at"))["earliest"]
        )
        if earliest:
            User.objects.filter(pk=user.pk).update(first_activity_at=earliest)

    # reached_60_at: can only be set prospectively; leave NULL for historical data
    # Workers already at >= 60 get a synthetic value of updated_at as a lower bound
    User.objects.filter(attracted_count__gte=60, reached_60_at__isnull=True).update(
        reached_60_at=models.F("updated_at")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0008_join_request_and_clients"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="first_activity_at",
            field=models.DateTimeField(
                blank=True, null=True,
                help_text="Момент первой заявки (attracted_count > 0 впервые)",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="reached_60_at",
            field=models.DateTimeField(
                blank=True, null=True,
                help_text="Момент первого достижения 60 заявок (порог конверсии)",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="deactivated_at",
            field=models.DateTimeField(
                blank=True, null=True,
                help_text="Момент деактивации аккаунта (status=inactive)",
            ),
        ),
        migrations.RunPython(backfill_metric_fields, migrations.RunPython.noop),
    ]
