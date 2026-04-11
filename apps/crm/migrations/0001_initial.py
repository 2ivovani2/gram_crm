import apps.crm.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Workspace",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120, verbose_name="Название")),
                ("slug", models.SlugField(max_length=60, unique=True, verbose_name="Слаг (URL)")),
                ("description", models.TextField(blank=True, verbose_name="Описание")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активен")),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="owned_workspaces",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Создатель",
                    ),
                ),
            ],
            options={"verbose_name": "Рабочее пространство", "verbose_name_plural": "Рабочие пространства", "ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="WorkspaceMembership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("role", models.CharField(choices=[("owner", "Главный админ (Владелец)"), ("finance", "Финансовый аналитик (Cash Flow)"), ("applications", "Менеджер по заявкам"), ("viewer", "Наблюдатель (только просмотр)")], default="viewer", max_length=20, verbose_name="Роль в CRM")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активен")),
                ("joined_at", models.DateTimeField(blank=True, null=True, verbose_name="Дата вступления")),
                (
                    "invited_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="crm_invitations_sent",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Кто пригласил",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="crm_memberships",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Пользователь",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="crm.workspace",
                        verbose_name="Пространство",
                    ),
                ),
            ],
            options={"verbose_name": "Участник пространства", "verbose_name_plural": "Участники пространства", "ordering": ["workspace", "role", "user__first_name"]},
        ),
        migrations.CreateModel(
            name="WeeklyPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("week_start", models.DateField(verbose_name="Начало недели (Пн)")),
                ("pp_plan", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="План ПП на неделю ($)")),
                ("privat_plan", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="План Приват на неделю ($)")),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="crm_weekly_plans_created",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Создал",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="weekly_plans",
                        to="crm.workspace",
                        verbose_name="Пространство",
                    ),
                ),
            ],
            options={"verbose_name": "Недельный план", "verbose_name_plural": "Недельные планы", "ordering": ["-week_start"]},
        ),
        migrations.CreateModel(
            name="FinanceEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("date", models.DateField(db_index=True, verbose_name="Дата")),
                ("income", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="Сумма поступлений ($)")),
                ("expenses", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="Сумма расходов / выплат ($)")),
                ("kb_screenshot", models.FileField(blank=True, null=True, upload_to=apps.crm.models._kb_upload_path, verbose_name="Скрин с КБ (файл)")),
                ("pp_earnings", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="Заработок с ПП за день ($)")),
                ("privat_earnings", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="Заработок с Привата за день ($)")),
                ("notes", models.TextField(blank=True, verbose_name="Примечания")),
                ("last_edited_at", models.DateTimeField(blank=True, null=True, verbose_name="Последнее редактирование")),
                (
                    "submitted_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="crm_finance_entries",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Внёс",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="finance_entries",
                        to="crm.workspace",
                        verbose_name="Пространство",
                    ),
                ),
            ],
            options={"verbose_name": "Финансовая запись", "verbose_name_plural": "Финансовые записи", "ordering": ["-date"]},
        ),
        migrations.CreateModel(
            name="ApplicationEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("date", models.DateField(db_index=True, verbose_name="Дата")),
                ("applications_count", models.PositiveIntegerField(default=0, verbose_name="Количество заявок за день")),
                ("applications_earnings", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="Заработок с заявок за день ($)")),
                ("notes", models.TextField(blank=True, verbose_name="Примечания")),
                ("last_edited_at", models.DateTimeField(blank=True, null=True, verbose_name="Последнее редактирование")),
                (
                    "submitted_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="crm_application_entries",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Внёс",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="application_entries",
                        to="crm.workspace",
                        verbose_name="Пространство",
                    ),
                ),
            ],
            options={"verbose_name": "Запись по заявкам", "verbose_name_plural": "Записи по заявкам", "ordering": ["-date"]},
        ),
        migrations.CreateModel(
            name="DailySummaryReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("date", models.DateField(db_index=True, verbose_name="Дата")),
                ("pp_earnings", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("privat_earnings", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("pp_plan_pct", models.DecimalField(decimal_places=2, default=0, max_digits=6, verbose_name="% выполнения плана ПП")),
                ("privat_plan_pct", models.DecimalField(decimal_places=2, default=0, max_digits=6, verbose_name="% выполнения плана Привата")),
                ("applications_count", models.PositiveIntegerField(default=0)),
                ("applications_earnings", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("cash_flow_income", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("cash_flow_expenses", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("cash_flow_balance", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("report_text", models.TextField(blank=True, verbose_name="Текст отчёта (plain)")),
                ("generated_at", models.DateTimeField(auto_now_add=True, verbose_name="Сгенерирован")),
                ("telegram_sent", models.BooleanField(default=False, verbose_name="Отправлен в Telegram")),
                (
                    "application_entry",
                    models.OneToOneField(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="summary_report",
                        to="crm.applicationentry",
                        verbose_name="Запись по заявкам",
                    ),
                ),
                (
                    "finance_entry",
                    models.OneToOneField(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="summary_report",
                        to="crm.financeentry",
                        verbose_name="Финансовая запись",
                    ),
                ),
                (
                    "generated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="crm_reports_generated",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Сгенерировал",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="daily_reports",
                        to="crm.workspace",
                        verbose_name="Пространство",
                    ),
                ),
            ],
            options={"verbose_name": "Сводный дневной отчёт", "verbose_name_plural": "Сводные дневные отчёты", "ordering": ["-date"]},
        ),
        migrations.CreateModel(
            name="DeadlineMiss",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("date", models.DateField(db_index=True, verbose_name="Дата пропуска")),
                ("finance_missing", models.BooleanField(default=False, verbose_name="Финансы не внесены")),
                ("applications_missing", models.BooleanField(default=False, verbose_name="Заявки не внесены")),
                ("notified_at", models.DateTimeField(blank=True, null=True, verbose_name="Уведомление отправлено")),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deadline_misses",
                        to="crm.workspace",
                        verbose_name="Пространство",
                    ),
                ),
            ],
            options={"verbose_name": "Пропуск дедлайна", "verbose_name_plural": "Пропуски дедлайна", "ordering": ["-date"]},
        ),
        # ── Unique constraints ────────────────────────────────────────────────
        migrations.AlterUniqueTogether(
            name="workspacemembership",
            unique_together={("workspace", "user")},
        ),
        migrations.AlterUniqueTogether(
            name="weeklyplan",
            unique_together={("workspace", "week_start")},
        ),
        migrations.AlterUniqueTogether(
            name="financeentry",
            unique_together={("workspace", "date")},
        ),
        migrations.AlterUniqueTogether(
            name="applicationentry",
            unique_together={("workspace", "date")},
        ),
        migrations.AlterUniqueTogether(
            name="dailysummaryreport",
            unique_together={("workspace", "date")},
        ),
        migrations.AlterUniqueTogether(
            name="deadlinemiss",
            unique_together={("workspace", "date")},
        ),
    ]
