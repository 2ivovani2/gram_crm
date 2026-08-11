from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("control", "0013_normalize_legacy_balance_debts"),
    ]

    operations = [
        migrations.AlterField(
            model_name="employeereport",
            name="editing_locked_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Текущий крайний срок редактирования или исправления",
                null=True,
                verbose_name="Блокировка редактирования",
            ),
        ),
    ]
