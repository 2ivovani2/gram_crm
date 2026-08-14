"""Authentik OIDC authentication for the private CRM.

Authentik proves identity; CRM remains the source of truth for authorization,
roles, balances, reports, and workspace membership. An OIDC identity can only
bind to an existing Telegram-backed CRM user through the explicit
``gramly_crm_telegram_username`` claim.
"""

from __future__ import annotations

import hashlib
import logging

from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from apps.users.models import User, UserStatus

logger = logging.getLogger(__name__)

CRM_USERNAME_CLAIM = "gramly_crm_telegram_username"
OIDC_ERROR_SESSION_KEY = "crm_oidc_error"


def normalize_telegram_username(value: object) -> str:
    """Normalize the explicit Authentik-to-CRM link claim."""

    return str(value or "").strip().lstrip("@").strip().lower()


def _subject_fingerprint(subject: str) -> str:
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()[:12]


class CRMOIDCBackend(OIDCAuthenticationBackend):
    """Bind an Authentik identity to an existing active CRM user."""

    def describe_user_by_claims(self, claims):
        subject = str(claims.get("sub") or "")
        return f"OIDC subject fingerprint {_subject_fingerprint(subject)}"

    def _set_failure(self, reason: str) -> None:
        request = getattr(self, "request", None)
        if request is not None:
            request.session[OIDC_ERROR_SESSION_KEY] = reason

    def _reject(self, reason: str, message: str, *, subject: str = "") -> None:
        self._set_failure(reason)
        logger.warning(
            "crm_oidc_rejected reason=%s subject_sha256=%s",
            reason,
            _subject_fingerprint(subject) if subject else "missing",
        )
        raise SuspiciousOperation(message)

    def verify_claims(self, claims):
        if not super().verify_claims(claims):
            self._set_failure("invalid_claims")
            return False
        verified = bool(claims.get("sub") and claims.get("email"))
        if not verified:
            self._set_failure("invalid_claims")
        return verified

    def filter_users_by_claims(self, claims):
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            self._set_failure("invalid_claims")
            return User.objects.none()

        # A stable subject always wins. Username and Authentik attribute changes
        # can never move an already-bound identity to another CRM account.
        bound = User.objects.filter(oidc_subject=subject)
        if bound.exists():
            return bound

        username = normalize_telegram_username(claims.get(CRM_USERNAME_CLAIM))
        if not username:
            self._set_failure("link_missing")
            logger.warning(
                "crm_oidc_rejected reason=link_missing subject_sha256=%s",
                _subject_fingerprint(subject),
            )
            return User.objects.none()

        candidates = User.objects.filter(
            Q(oidc_subject__isnull=True) | Q(oidc_subject=""),
            telegram_username__iexact=username,
            oidc_binding_blocked=False,
            is_active=True,
            status=UserStatus.ACTIVE,
        )
        count = candidates.count()
        if count == 1:
            return candidates

        if User.objects.filter(
            telegram_username__iexact=username,
            oidc_binding_blocked=True,
        ).exists():
            reason = "link_blocked"
        elif User.objects.filter(
            telegram_username__iexact=username,
        ).exclude(Q(oidc_subject__isnull=True) | Q(oidc_subject="")).exists():
            reason = "link_occupied"
        elif count > 1:
            reason = "link_ambiguous"
        else:
            reason = "link_not_found"
        self._set_failure(reason)
        logger.warning(
            "crm_oidc_rejected reason=%s subject_sha256=%s candidate_count=%s",
            reason,
            _subject_fingerprint(subject),
            count,
        )
        return User.objects.none()

    def create_user(self, claims):
        # ``mozilla-django-oidc`` calls this after an empty queryset. Preserve
        # the actionable reason recorded by ``filter_users_by_claims`` instead
        # of replacing it with a generic self-registration error.
        request = getattr(self, "request", None)
        if request is not None and request.session.get(OIDC_ERROR_SESSION_KEY):
            raise SuspiciousOperation("OIDC identity is not linked to CRM")
        self._reject(
            "self_registration_disabled",
            "OIDC self-registration is disabled",
            subject=str(claims.get("sub") or ""),
        )

    def update_user(self, user, claims):
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            self._reject("invalid_claims", "OIDC subject is missing")

        with transaction.atomic():
            locked = User.objects.select_for_update().get(pk=user.pk)
            if not locked.is_active or locked.status != UserStatus.ACTIVE:
                self._reject(
                    "user_inactive",
                    "CRM user is inactive",
                    subject=subject,
                )

            if locked.oidc_binding_blocked:
                self._reject(
                    "link_blocked",
                    "CRM identity binding is blocked",
                    subject=subject,
                )

            if locked.oidc_subject and locked.oidc_subject != subject:
                self._reject(
                    "link_occupied",
                    "CRM user is already bound to another OIDC subject",
                    subject=subject,
                )

            is_new_binding = not locked.oidc_subject
            if is_new_binding:
                claimed_username = normalize_telegram_username(
                    claims.get(CRM_USERNAME_CLAIM)
                )
                if not claimed_username:
                    self._reject(
                        "link_missing",
                        "Explicit CRM identity claim is missing",
                        subject=subject,
                    )
                if normalize_telegram_username(locked.telegram_username) != claimed_username:
                    self._reject(
                        "link_conflict",
                        "CRM identity claim no longer matches the selected user",
                        subject=subject,
                    )
                duplicate_count = User.objects.filter(
                    telegram_username__iexact=claimed_username,
                    is_active=True,
                    status=UserStatus.ACTIVE,
                ).count()
                if duplicate_count != 1:
                    self._reject(
                        "link_ambiguous",
                        "CRM identity claim is ambiguous",
                        subject=subject,
                    )
                locked.oidc_subject = subject
                locked.oidc_linked_at = timezone.now()

            changed_fields = []
            if is_new_binding:
                changed_fields.extend(["oidc_subject", "oidc_linked_at"])

            email = str(claims.get("email") or "").strip()
            if email and locked.email != email:
                locked.email = email
                changed_fields.append("email")

            if changed_fields:
                changed_fields.append("updated_at")
                locked.save(update_fields=changed_fields)

            if is_new_binding:
                logger.info(
                    "crm_oidc_linked crm_user_id=%s subject_sha256=%s",
                    locked.pk,
                    _subject_fingerprint(subject),
                )
            return locked
