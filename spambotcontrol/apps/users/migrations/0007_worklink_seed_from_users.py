"""
Data migration: create initial WorkLink records from existing User.work_url + User.attracted_count.

For each user that has either a work_url or attracted_count > 0, we create one active WorkLink
that mirrors their current state. Users with no link and count=0 are skipped.

After this migration, recalculate_balance() will read from WorkLinks correctly.
"""
from django.db import migrations
from django.utils import timezone


def seed_work_links(apps, schema_editor):
    User = apps.get_model("users", "User")
    WorkLink = apps.get_model("users", "WorkLink")

    batch = []
    for user in User.objects.all():
        # Only create a WorkLink if the user actually had data
        if user.work_url or user.attracted_count:
            batch.append(WorkLink(
                user=user,
                url=user.work_url or "",
                attracted_count=user.attracted_count or 0,
                is_active=True,
                note="Мигрировано из User.work_url / attracted_count",
            ))

    if batch:
        WorkLink.objects.bulk_create(batch)


def reverse_seed(apps, schema_editor):
    WorkLink = apps.get_model("users", "WorkLink")
    WorkLink.objects.filter(note="Мигрировано из User.work_url / attracted_count").delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0006_worklink'),
    ]

    operations = [
        migrations.RunPython(seed_work_links, reverse_code=reverse_seed),
    ]
