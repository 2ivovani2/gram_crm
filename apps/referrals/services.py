from __future__ import annotations
from django.conf import settings
from .models import ReferralLink, ReferralSettings


class ReferralService:

    @staticmethod
    def get_or_create_link(user) -> ReferralLink:
        link, _ = ReferralLink.objects.get_or_create(user=user)
        return link

    @staticmethod
    def get_referral_url(user) -> str:
        link = ReferralService.get_or_create_link(user)
        bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "")
        return f"https://t.me/{bot_username}?start=ref_{link.token}"

    @staticmethod
    def resolve_token(token: str):
        """Returns the referrer User for the given token, or None."""
        try:
            return ReferralLink.objects.select_related("user").get(token=token).user
        except ReferralLink.DoesNotExist:
            return None

    @staticmethod
    def get_settings() -> ReferralSettings:
        return ReferralSettings.get()

    @staticmethod
    def set_rate(rate_percent: float, updated_by) -> ReferralSettings:
        obj = ReferralSettings.get()
        obj.rate_percent = rate_percent
        obj.updated_by = updated_by
        obj.save(update_fields=["rate_percent", "updated_by", "updated_at"])
        return obj
