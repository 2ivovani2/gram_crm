"""Authentik OIDC authentication for the private CRM.

Authentik proves identity; CRM remains the source of truth for authorization,
roles, status, balances, and workspace membership. Self-registration is never
allowed.
"""

from __future__ import annotations

import logging

from django.core.exceptions import SuspiciousOperation
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from apps.users.models import User, UserStatus

logger = logging.getLogger(__name__)


class CRMOIDCBackend(OIDCAuthenticationBackend):
    """Bind an Authentik identity to an existing active CRM user."""

    def describe_user_by_claims(self, claims):
        return f"OIDC subject {claims.get('sub')!r}"

    def verify_claims(self, claims):
        if not super().verify_claims(claims):
            return False
        return bool(
            claims.get("sub")
            and claims.get("preferred_username")
            and claims.get("email")
        )

    def filter_users_by_claims(self, claims):
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            return User.objects.none()

        bound = User.objects.filter(oidc_subject=subject)
        if bound.exists():
            return bound

        username = str(claims.get("preferred_username") or "").strip().lstrip("@")
        if not username:
            return User.objects.none()

        # Initial binding is deliberately limited to an existing CRM identity.
        # Authentik administrators control usernames, while CRM controls roles.
        return User.objects.filter(
            oidc_subject__isnull=True,
            telegram_username__iexact=username,
        )

    def create_user(self, claims):
        raise SuspiciousOperation("OIDC self-registration is disabled")

    def update_user(self, user, claims):
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise SuspiciousOperation("OIDC subject is missing")
        if user.oidc_subject and user.oidc_subject != subject:
            raise SuspiciousOperation("CRM user is already bound to another OIDC subject")
        if not user.is_active or user.status != UserStatus.ACTIVE:
            raise SuspiciousOperation("CRM user is inactive")

        changed_fields = []
        if not user.oidc_subject:
            user.oidc_subject = subject
            changed_fields.append("oidc_subject")

        email = str(claims.get("email") or "").strip()
        if email and user.email != email:
            user.email = email
            changed_fields.append("email")

        if changed_fields:
            changed_fields.append("updated_at")
            user.save(update_fields=changed_fields)
            logger.info("Bound CRM user id=%s to Authentik OIDC", user.pk)
        return user
