from __future__ import annotations
import logging
from typing import Optional
from django.db import transaction
from .models import InviteKey, InviteActivation
from apps.users.models import User

logger = logging.getLogger(__name__)


class InviteValidationError(Exception):
    """Raised when invite key validation fails. Message is user-facing."""


class InviteService:

    @staticmethod
    def validate_and_activate(user: User, raw_key: str) -> InviteKey:
        """
        Validate invite key and activate the user.
        Raises InviteValidationError with a user-facing message on any failure.
        """
        key_str = raw_key.strip().upper()

        if user.is_activated:
            raise InviteValidationError("Ваш аккаунт уже активирован.")

        try:
            invite_key = InviteKey.objects.get(key=key_str)
        except InviteKey.DoesNotExist:
            raise InviteValidationError("Ключ не найден. Проверьте правильность ввода.")

        if not invite_key.is_active:
            raise InviteValidationError("Этот ключ деактивирован.")

        if invite_key.is_expired:
            raise InviteValidationError("Срок действия ключа истёк.")

        if invite_key.is_exhausted:
            raise InviteValidationError("Лимит использований ключа исчерпан.")

        if InviteActivation.objects.filter(key=invite_key, user=user).exists():
            raise InviteValidationError("Этот ключ уже был использован вашим аккаунтом.")

        with transaction.atomic():
            InviteActivation.objects.create(key=invite_key, user=user)
            InviteKey.objects.filter(pk=invite_key.pk).update(uses_count=invite_key.uses_count + 1)
            user.activate()
            # Link referral: curator-created keys make the new user a referral of the curator
            if (
                invite_key.created_by_id
                and not user.referred_by_id
                and invite_key.created_by.is_curator()
            ):
                User.objects.filter(pk=user.pk).update(referred_by_id=invite_key.created_by_id)

        logger.info("User %s activated with key %s", user.telegram_id, key_str)
        return invite_key

    @staticmethod
    def create_key(
        created_by: User,
        label: str = "",
        max_uses: Optional[int] = None,
        expires_at=None,
    ) -> InviteKey:
        return InviteKey.objects.create(
            label=label,
            max_uses=max_uses,
            expires_at=expires_at,
            created_by=created_by,
        )

    @staticmethod
    def toggle_active(key: InviteKey) -> InviteKey:
        key.is_active = not key.is_active
        key.save(update_fields=["is_active"])
        return key

    @staticmethod
    def get_keys_list(page: int = 1, page_size: int = 10, created_by: User | None = None) -> tuple[list[InviteKey], int]:
        from apps.common.utils import paginate
        qs = InviteKey.objects.select_related("created_by").order_by("-created_at")
        if created_by is not None:
            qs = qs.filter(created_by=created_by)
        items, total, _ = paginate(qs, page, page_size)
        return items, total

    @staticmethod
    def get_activations(key: InviteKey, page: int = 1, page_size: int = 10) -> tuple[list[InviteActivation], int]:
        from apps.common.utils import paginate
        qs = InviteActivation.objects.filter(key=key).select_related("user").order_by("-activated_at")
        items, total, _ = paginate(qs, page, page_size)
        return items, total
