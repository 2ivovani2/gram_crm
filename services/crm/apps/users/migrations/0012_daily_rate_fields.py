from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0011_alter_user_role_alter_user_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='daily_rate',
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=10,
                verbose_name='Ставка в день (₽)',
                help_text='Фиксированная сумма, начисляемая ежедневно в настроенное время',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='daily_accrued',
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=12,
                verbose_name='Накоплено по ставке (₽)',
                help_text='Суммарно начислено по ежедневной ставке за всё время',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='daily_rate_last_accrued_date',
            field=models.DateField(
                null=True, blank=True,
                verbose_name='Дата последнего начисления ставки',
            ),
        ),
    ]
