from decimal import Decimal
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("stats", "0005_missedday"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="GlobalRate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("worker_rate", models.DecimalField(
                    decimal_places=2, default=Decimal("0"), max_digits=10,
                    verbose_name="Ставка воркера (₽/заявка)",
                    help_text="Сколько ₽ воркер получает за одну заявку",
                )),
                ("referral_rate", models.DecimalField(
                    decimal_places=2, default=Decimal("0"), max_digits=10,
                    verbose_name="Ставка реферала (₽/заявка)",
                    help_text="Сколько ₽ реферрер получает за одну заявку реферала",
                )),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+", to="users.user",
                )),
            ],
            options={
                "verbose_name": "Глобальные ставки",
                "verbose_name_plural": "Глобальные ставки",
            },
        ),
    ]
