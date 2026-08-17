from decimal import Decimal

from django.db import migrations
from django.db.models import Sum
from django.utils import timezone


ZERO = Decimal("0")


def normalize_legacy_balance_debts(apps, schema_editor):
    """
    Remove hidden negative balances left by the legacy manual-balance editor.

    The old editor overwrote gross accruals while approved withdrawals remained
    in history.  That could leave gross < withdrawn, so future daily credits
    were consumed by an invisible historical deficit even though the bot had
    always displayed zero.  Preserve that zero baseline and let any credit
    accrued today remain available.
    """
    User = apps.get_model("users", "User")
    WorkLink = apps.get_model("users", "WorkLink")
    WithdrawalRequest = apps.get_model("withdrawals", "WithdrawalRequest")
    Penalty = apps.get_model("control", "Penalty")
    today = timezone.localdate()

    for user in User.objects.filter(manual_balance_adjustment=ZERO).iterator():
        personal_count = (
            WorkLink.objects.filter(user_id=user.pk)
            .aggregate(total=Sum("attracted_count"))["total"]
            or 0
        )
        referral_count = (
            WorkLink.objects.filter(user__referred_by_id=user.pk)
            .aggregate(total=Sum("attracted_count"))["total"]
            or 0
        )
        personal_earned = Decimal(personal_count) * (user.personal_rate or ZERO)
        referral_earned = Decimal(referral_count) * (user.referral_rate or ZERO)
        daily_accrued = user.daily_accrued or ZERO

        # If today's rate has already run, normalize the legacy balance as it
        # stood before that credit so the new money remains available.
        today_credit = (
            (user.daily_rate or ZERO)
            if user.daily_rate_last_accrued_date == today
            else ZERO
        )

        withdrawn = (
            WithdrawalRequest.objects.filter(user_id=user.pk, status="approved")
            .aggregate(total=Sum("amount"))["total"]
            or ZERO
        )
        penalties = (
            Penalty.objects.filter(user_id=user.pk, status="accepted")
            .aggregate(total=Sum("amount"))["total"]
            or ZERO
        )

        raw_before_today = (
            personal_earned
            + referral_earned
            + daily_accrued
            - today_credit
            - withdrawn
            - penalties
        )
        if raw_before_today < ZERO:
            User.objects.filter(pk=user.pk).update(
                manual_balance_adjustment=-raw_before_today
            )


def noop_reverse(apps, schema_editor):
    # This is a data repair. Restoring invisible legacy debt would be harmful.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("control", "0012_controlsettings_withdrawal_processors"),
        ("users", "0013_user_manual_balance_adjustment"),
        ("withdrawals", "0004_alter_cryptoaddress_id"),
    ]

    operations = [
        migrations.RunPython(
            normalize_legacy_balance_debts,
            reverse_code=noop_reverse,
        ),
    ]
