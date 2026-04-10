from __future__ import annotations
from django.conf import settings
from .models import ReferralLink


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
