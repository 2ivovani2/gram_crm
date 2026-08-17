from __future__ import annotations
import secrets
import string
from typing import Optional
from django.db import models
from django.utils import timezone


def _generate_key() -> str:
    """Generate a 12-character uppercase alphanumeric invite key."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(12))


class InviteKey(models.Model):
    key = models.CharField(max_length=64, unique=True, default=_generate_key, db_index=True)
    label = models.CharField(max_length=200, blank=True, help_text="Internal label for admin reference")

    is_active = models.BooleanField(default=True)

    # Usage limits — None means unlimited
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    uses_count = models.PositiveIntegerField(default=0)

    expires_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_invite_keys",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Инвайт-ключ"
        verbose_name_plural = "Инвайт-ключи"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.key} [{self.get_status_label()}]"

    # ── Computed status ───────────────────────────────────────────────────────

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and timezone.now() > self.expires_at

    @property
    def is_exhausted(self) -> bool:
        return self.max_uses is not None and self.uses_count >= self.max_uses

    @property
    def is_valid(self) -> bool:
        return self.is_active and not self.is_expired and not self.is_exhausted

    @property
    def remaining_uses(self) -> Optional[int]:
        if self.max_uses is None:
            return None
        return max(0, self.max_uses - self.uses_count)

    def get_status_label(self) -> str:
        if not self.is_active:
            return "inactive"
        if self.is_expired:
            return "expired"
        if self.is_exhausted:
            return "exhausted"
        return "active"


class InviteActivation(models.Model):
    """Log of each successful invite key usage."""

    key = models.ForeignKey(InviteKey, on_delete=models.PROTECT, related_name="activations")
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="invite_activations")
    activated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Активация инвайта"
        verbose_name_plural = "Активации инвайтов"
        unique_together = [["key", "user"]]
        ordering = ["-activated_at"]

    def __str__(self) -> str:
        return f"{self.user} → {self.key_id}"
