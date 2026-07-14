from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0012_daily_rate_fields'),
        ('withdrawals', '0002_alter_withdrawalrequest_status'),
    ]

    operations = [
        migrations.CreateModel(
            name='CryptoAddress',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100, verbose_name='Название')),
                ('address', models.CharField(max_length=200, verbose_name='Адрес TRC20')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='crypto_addresses',
                    to='users.user',
                    verbose_name='Пользователь',
                )),
            ],
            options={
                'verbose_name': 'Крипто-адрес',
                'verbose_name_plural': 'Крипто-адреса',
                'ordering': ['name'],
            },
        ),
    ]
