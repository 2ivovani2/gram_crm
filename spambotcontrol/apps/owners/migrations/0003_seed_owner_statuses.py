from django.db import migrations


DEFAULTS = (
    ("Рабочий", "#596623", "", "Аккаунт доступен для штатной работы."),
    ("Требует проверки", "#BD8330", "", "Нужна ручная проверка сотрудника."),
    ("Не использовать", "#A63E36", "", "Работа с аккаунтом временно запрещена."),
    ("Ожидает проверки", "#55CFE8", "", "Новая запись ожидает первичной проверки."),
)


def seed_statuses(apps, schema_editor):
    Workspace = apps.get_model("crm", "Workspace")
    OwnerStatus = apps.get_model("owners", "OwnerStatus")
    for workspace in Workspace.objects.iterator():
        for name, color, emoji, description in DEFAULTS:
            OwnerStatus.objects.get_or_create(
                workspace=workspace,
                name=name,
                defaults={
                    "color": color,
                    "emoji": emoji,
                    "description": description,
                    "is_system": True,
                },
            )


class Migration(migrations.Migration):
    dependencies = [("owners", "0002_telegramowner_deleted_at")]
    operations = [migrations.RunPython(seed_statuses, migrations.RunPython.noop)]
