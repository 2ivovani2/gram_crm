import pytest
from django.core.exceptions import SuspiciousOperation
from django.urls import reverse

from apps.crm.auth import CRMOIDCBackend
from apps.users.models import User, UserRole, UserStatus


pytestmark = pytest.mark.django_db


def make_user(**overrides):
    values = {
        "telegram_id": 10001,
        "telegram_username": "i_vovani",
        "username": "tg_10001",
        "role": UserRole.ADMIN,
        "status": UserStatus.ACTIVE,
        "is_active": True,
        "is_activated": True,
    }
    values.update(overrides)
    return User.objects.create(**values)


def claims(**overrides):
    values = {
        "sub": "stable-authentik-subject",
        "preferred_username": "i_vovani",
        "email": "owner@example.com",
    }
    values.update(overrides)
    return values


def test_initial_oidc_login_matches_existing_telegram_username(settings):
    settings.OIDC_RP_CLIENT_ID = "client"
    settings.OIDC_RP_CLIENT_SECRET = "secret"
    user = make_user()
    backend = CRMOIDCBackend()

    matched = backend.filter_users_by_claims(claims())
    assert list(matched) == [user]

    backend.update_user(user, claims())
    user.refresh_from_db()
    assert user.oidc_subject == "stable-authentik-subject"
    assert user.email == "owner@example.com"


def test_bound_subject_takes_precedence_over_changed_username(settings):
    settings.OIDC_RP_CLIENT_ID = "client"
    settings.OIDC_RP_CLIENT_SECRET = "secret"
    user = make_user(oidc_subject="stable-authentik-subject")
    backend = CRMOIDCBackend()

    matched = backend.filter_users_by_claims(
        claims(preferred_username="renamed-in-authentik")
    )
    assert list(matched) == [user]


def test_oidc_does_not_match_unknown_or_already_bound_identity(settings):
    settings.OIDC_RP_CLIENT_ID = "client"
    settings.OIDC_RP_CLIENT_SECRET = "secret"
    make_user(oidc_subject="another-subject")
    backend = CRMOIDCBackend()

    assert not backend.filter_users_by_claims(claims()).exists()
    with pytest.raises(SuspiciousOperation, match="self-registration"):
        backend.create_user(claims())


def test_inactive_crm_user_is_rejected(settings):
    settings.OIDC_RP_CLIENT_ID = "client"
    settings.OIDC_RP_CLIENT_SECRET = "secret"
    user = make_user(status=UserStatus.INACTIVE)
    backend = CRMOIDCBackend()

    with pytest.raises(SuspiciousOperation, match="inactive"):
        backend.update_user(user, claims())


def test_crm_login_starts_oidc_and_telegram_callback_is_removed(client):
    response = client.get(reverse("crm:login"))
    assert response.status_code == 302
    assert response.url.startswith(reverse("oidc_authentication_init"))
    assert client.get("/crm/auth/callback/").status_code == 404
