from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="financeentry",
            name="kb_balance",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=14,
                verbose_name="Баланс КБ ($)",
                help_text="Текущий баланс на счёте КБ (в долларах)",
            ),
        ),
        migrations.AddField(
            model_name="dailysummaryreport",
            name="kb_balance_snapshot",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=14,
                verbose_name="Баланс КБ ($) — снимок",
            ),
        ),
    ]
