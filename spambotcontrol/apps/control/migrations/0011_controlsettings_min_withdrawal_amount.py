from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('control', '0010_controlsettings_daily_rate_hour'),
    ]

    operations = [
        migrations.AddField(
            model_name='controlsettings',
            name='min_withdrawal_amount',
            field=models.DecimalField(
                decimal_places=2, default=1000, max_digits=12,
                verbose_name='Минимальная сумма вывода (₽)',
            ),
        ),
    ]
