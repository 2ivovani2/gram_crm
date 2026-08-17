from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0012_daily_rate_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="manual_balance_adjustment",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text=(
                    "Корректировка, позволяющая администратору выставить текущий "
                    "доступный баланс без изменения истории начислений и выводов"
                ),
                max_digits=12,
                verbose_name="Ручная корректировка баланса (₽)",
            ),
        ),
    ]
