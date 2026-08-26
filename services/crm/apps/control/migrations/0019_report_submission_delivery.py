from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("control", "0018_financialconditionchange"),
        ("users", "0015_user_oidc_binding_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeereport",
            name="submission_completed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Устанавливается после явного подтверждения сотрудником",
                null=True,
                verbose_name="Время завершения подачи",
            ),
        ),
        migrations.CreateModel(
            name="ReportSubmissionReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("submission_key", models.CharField(max_length=160, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="submission_receipts",
                        to="control.employeereport",
                    ),
                ),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.CreateModel(
            name="ReportSubmissionDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Ожидает"), ("retry", "Повтор"), ("sent", "Отправлено")], default="pending", max_length=16)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("last_error", models.CharField(blank=True, max_length=255)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "recipient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="report_submission_deliveries",
                        to="users.user",
                    ),
                ),
                (
                    "report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="submission_deliveries",
                        to="control.employeereport",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="reportsubmissiondelivery",
            constraint=models.UniqueConstraint(
                fields=("report", "recipient"),
                name="uniq_report_submission_delivery_recipient",
            ),
        ),
    ]
