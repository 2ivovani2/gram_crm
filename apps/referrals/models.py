import secrets
from django.db import models


def _generate_token() -> str:
    return secrets.token_urlsafe(8)


class ReferralLink(models.Model):
    """
    One referral link per activated worker.
    Created lazily via ReferralService.get_or_create_link().
    Deep-link format: https://t.me/{bot_username}?start=ref_{token}
    """
    user = models.OneToOneField(
        "users.User",
        on_delete=models.CASCADE,
        related_name="referral_link",
    )
    token = models.CharField(max_length=20, unique=True, default=_generate_token, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Реферальная ссылка"
        verbose_name_plural = "Реферальные ссылки"

    def __str__(self) -> str:
        return f"ref_{self.token} → {self.user}"


class ReferralSettings(models.Model):
    """
    Singleton: global referral program config.
    Access via ReferralSettings.get() — never create more than one row.
    """
    rate_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Процент от заработка реферала, начисляемый рефереру (0 = отключено)",
    )
    updated_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки реферальной программы"
        verbose_name_plural = "Настройки реферальной программы"

    def __str__(self) -> str:
        return f"Реферальная ставка: {self.rate_percent}%"

    @classmethod
    def get(cls) -> "ReferralSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
