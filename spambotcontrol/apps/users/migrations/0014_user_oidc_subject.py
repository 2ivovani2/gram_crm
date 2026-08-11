from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0013_user_manual_balance_adjustment"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="oidc_subject",
            field=models.CharField(
                blank=True,
                help_text="Stable Authentik subject bound to this CRM user",
                max_length=255,
                null=True,
                unique=True,
                verbose_name="OIDC subject",
            ),
        ),
    ]
