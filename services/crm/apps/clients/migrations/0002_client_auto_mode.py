from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0001_join_request_and_clients"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="channel_id",
            field=models.BigIntegerField(
                blank=True, null=True,
                verbose_name="Telegram Chat ID",
                help_text="ID канала/группы (число, напр. -1001234567890). Нужен для авто-режима.",
            ),
        ),
        migrations.AddField(
            model_name="client",
            name="channel_username",
            field=models.CharField(
                blank=True, max_length=255,
                verbose_name="@username канала",
                help_text="Для отображения (заполняется автоматически при проверке прав)",
            ),
        ),
        migrations.AddField(
            model_name="client",
            name="auto_mode",
            field=models.BooleanField(
                default=False,
                verbose_name="Авто-режим",
                help_text="Если включён — бот генерирует уникальные invite links для воркеров",
            ),
        ),
        migrations.AddField(
            model_name="client",
            name="bot_check_status",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("unchecked", "Не проверено"),
                    ("ok", "Права подтверждены"),
                    ("not_admin", "Бот не администратор"),
                    ("no_permissions", "Недостаточно прав"),
                    ("no_access", "Нет доступа к каналу"),
                ],
                default="unchecked",
                verbose_name="Статус проверки бота",
            ),
        ),
        migrations.AddField(
            model_name="client",
            name="bot_check_detail",
            field=models.TextField(
                blank=True,
                verbose_name="Детали проверки",
            ),
        ),
        migrations.AddField(
            model_name="client",
            name="bot_check_at",
            field=models.DateTimeField(
                blank=True, null=True,
                verbose_name="Дата последней проверки",
            ),
        ),
    ]
