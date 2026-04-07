import datetime
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stats', '0002_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RateConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('worker_share', models.DecimalField(
                    decimal_places=4, default=Decimal('0.2500'), max_digits=6,
                    verbose_name='Доля работника (0–1)',
                    help_text='Например 0.25 = 25 % от ставки клиента',
                )),
                ('referral_share', models.DecimalField(
                    decimal_places=4, default=Decimal('0.1389'), max_digits=6,
                    verbose_name='Доля реферала (0–1)',
                    help_text='Например 0.1389 ≈ 13.89 % от ставки клиента',
                )),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Конфигурация ставок',
                'verbose_name_plural': 'Конфигурация ставок',
            },
        ),
        migrations.CreateModel(
            name='DailyReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(
                    db_index=True, default=datetime.date.today, unique=True,
                    verbose_name='Дата',
                )),
                ('link', models.URLField(blank=True, max_length=500, verbose_name='Ссылка на пост/канал')),
                ('client_nick', models.CharField(blank=True, max_length=255, verbose_name='Ник клиента')),
                ('client_rate', models.DecimalField(
                    decimal_places=2, default=Decimal('0'), max_digits=10,
                    verbose_name='Ставка клиента (руб./чел.)',
                )),
                ('total_applications', models.PositiveIntegerField(
                    default=0, verbose_name='Заявок за день (шт.)',
                )),
                ('worker_rate', models.DecimalField(
                    decimal_places=2, default=Decimal('0'), max_digits=10,
                    verbose_name='Ставка работника (руб./чел.)',
                )),
                ('referral_rate', models.DecimalField(
                    decimal_places=2, default=Decimal('0'), max_digits=10,
                    verbose_name='Ставка реферала (руб./чел.)',
                )),
                ('our_profit', models.DecimalField(
                    decimal_places=2, default=Decimal('0'), max_digits=10,
                    verbose_name='Наша прибыль (руб./чел.)',
                )),
                ('broadcast_sent', models.BooleanField(
                    default=False,
                    help_text='Флаг защиты от повторной отправки',
                    verbose_name='Рассылка отправлена',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='daily_reports',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Внёс',
                )),
            ],
            options={
                'verbose_name': 'Дневной отчёт',
                'verbose_name_plural': 'Дневные отчёты',
                'ordering': ['-date'],
            },
        ),
    ]
