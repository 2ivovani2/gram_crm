from django.db import migrations


class Migration(migrations.Migration):
    """
    Drops the ReferralSettings model (and its rate_percent field).
    Per-user rates (personal_rate, referral_rate on User) replaced global rate_percent.
    """

    dependencies = [
        ("referrals", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ReferralSettings",
        ),
    ]
