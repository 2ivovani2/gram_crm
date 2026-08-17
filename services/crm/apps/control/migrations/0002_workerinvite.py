from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("control", "0001_initial"),
        ("users", "0010_alter_user_role"),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkerInvite",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Ожидает ответа"), ("accepted", "Принято"), ("declined", "Отклонено")], db_index=True, default="pending", max_length=20)),
                ("bot_message_id", models.BigIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("responded_at", models.DateTimeField(blank=True, null=True)),
                ("invited_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sent_worker_invites", to="users.user", verbose_name="Кто пригласил")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="worker_invites", to="users.user", verbose_name="Приглашённый")),
            ],
            options={"verbose_name": "Приглашение сотрудника", "verbose_name_plural": "Приглашения сотрудников", "ordering": ["-created_at"]},
        ),
    ]
