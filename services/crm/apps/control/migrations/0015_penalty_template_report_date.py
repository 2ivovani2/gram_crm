from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("control", "0014_alter_employeereport_editing_locked_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="penalty",
            name="report_date",
            field=models.DateField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="Отчётная дата штрафа",
            ),
        ),
        migrations.AddField(
            model_name="penalty",
            name="template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="missed_report_penalties",
                to="control.reporttemplate",
                verbose_name="Пропущенный шаблон отчёта",
            ),
        ),
        migrations.AddConstraint(
            model_name="penalty",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("report_date__isnull", False),
                    ("template__isnull", False),
                    ("type", "auto"),
                ),
                fields=("user", "template", "report_date"),
                name="uniq_auto_penalty_user_template_date",
            ),
        ),
    ]
