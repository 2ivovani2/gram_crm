from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0014_user_oidc_subject"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="oidc_binding_blocked",
            field=models.BooleanField(
                default=False,
                help_text="Prevent automatic rebinding until an administrator allows it",
                verbose_name="OIDC binding blocked",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="oidc_linked_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Timestamp of the latest successful Authentik identity binding",
                null=True,
                verbose_name="OIDC linked at",
            ),
        ),
    ]
