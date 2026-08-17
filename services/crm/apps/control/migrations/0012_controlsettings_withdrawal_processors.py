from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('control', '0011_controlsettings_min_withdrawal_amount'),
        ('users', '0012_daily_rate_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='controlsettings',
            name='withdrawal_processors',
            field=models.ManyToManyField(
                blank=True,
                related_name='as_withdrawal_processor',
                to='users.user',
                verbose_name='Дополнительные обработчики выводов',
            ),
        ),
    ]
