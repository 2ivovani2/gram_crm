from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("control", "0004_alter_reporttemplate_options_and_more"),
        ("users", "0011_alter_user_role_alter_user_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeadlineNotificationLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("deadline_date", models.DateField(verbose_name="Дата дедлайна")),
                ("slot", models.CharField(
                    max_length=5,
                    choices=[("23:00", "За час (23:00)"), ("23:30", "За 30 мин (23:30)"),
                             ("23:45", "За 15 мин (23:45)"), ("00:00", "Пропущен (00:00)")],
                    verbose_name="Слот",
                )),
                ("status", models.CharField(
                    max_length=10,
                    choices=[("sent", "Доставлено"), ("skipped", "Пропущено (данные внесены)"),
                             ("error", "Ошибка отправки")],
                    verbose_name="Статус",
                )),
                ("error_text", models.TextField(blank=True, default="", verbose_name="Ошибка")),
                ("telegram_id", models.BigIntegerField(verbose_name="Telegram ID")),
                ("missing_items", models.JSONField(default=list, verbose_name="Что не внесено")),
                ("balance_snapshot", models.DecimalField(
                    max_digits=12, decimal_places=2, null=True, blank=True,
                    verbose_name="Баланс на момент отправки",
                )),
                ("available_snapshot", models.DecimalField(
                    max_digits=12, decimal_places=2, null=True, blank=True,
                    verbose_name="Доступно к выводу",
                )),
                ("penalty_snapshot", models.DecimalField(
                    max_digits=12, decimal_places=2, null=True, blank=True,
                    verbose_name="Штраф",
                )),
                ("attempted_at", models.DateTimeField(auto_now_add=True, verbose_name="Время попытки")),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="deadline_notifications",
                    to="users.user",
                    verbose_name="Пользователь",
                )),
            ],
            options={
                "verbose_name": "Лог уведомления о дедлайне",
                "verbose_name_plural": "Лог уведомлений о дедлайне",
                "ordering": ["-attempted_at"],
            },
        ),
        migrations.AlterUniqueTogether(
            name="deadlinenotificationlog",
            unique_together={("user", "deadline_date", "slot")},
        ),
    ]
