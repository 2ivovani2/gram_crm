from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0002_add_kb_balance"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="workspacemembership", name="permission_overrides", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="workspacemembership", name="access_version", field=models.PositiveIntegerField(default=0)),
        migrations.CreateModel(
            name="AccessChange",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=32)),
                ("before", models.JSONField(default=dict)),
                ("after", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="crm.workspace")),
                ("actor", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="access_changes_made", to=settings.AUTH_USER_MODEL)),
                ("target", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="access_changes", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
