from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('control', '0009_alter_fields_lifecycle'),
    ]

    operations = [
        migrations.AddField(
            model_name='controlsettings',
            name='daily_rate_hour',
            field=models.PositiveSmallIntegerField(
                default=20,
                verbose_name='Час начисления ежедневной ставки (МСК, 0–23)',
            ),
        ),
    ]
