import pytest
from django.core.exceptions import SuspiciousOperation
from django.urls import reverse

from apps.crm.auth import (
    CRM_TELEGRAM_ID_CLAIM,
    OIDC_ERROR_SESSION_KEY,
    CRMOIDCBackend,
    normalize_telegram_id,
)
from apps.crm.identity import terminate_user_sessions
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
        "preferred_username": "unrelated-sso-login",
        "email": "owner@example.com",
        CRM_TELEGRAM_ID_CLAIM: "10001",
    }
    values.update(overrides)
    return values


def test_initial_oidc_login_matches_explicit_telegram_id_claim(settings):
    settings.OIDC_RP_CLIENT_ID = "client"
    settings.OIDC_RP_CLIENT_SECRET = "secret"
    user = make_user()
    backend = CRMOIDCBackend()

    matched = backend.filter_users_by_claims(claims())
    assert list(matched) == [user]

    backend.update_user(user, claims())
    user.refresh_from_db()
    assert user.oidc_subject == "stable-authentik-subject"
    assert user.oidc_linked_at is not None
    assert user.email == "owner@example.com"


def test_oidc_self_registration_is_disabled_in_settings(settings):
    assert settings.OIDC_CREATE_USER is False


def test_bound_subject_takes_precedence_over_changed_telegram_id(settings):
    settings.OIDC_RP_CLIENT_ID = "client"
    settings.OIDC_RP_CLIENT_SECRET = "secret"
    user = make_user(oidc_subject="stable-authentik-subject")
    backend = CRMOIDCBackend()

    matched = backend.filter_users_by_claims(
        claims(
            preferred_username="renamed-in-authentik",
            **{CRM_TELEGRAM_ID_CLAIM: "99999"},
        )
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


def test_preferred_username_is_never_used_as_binding_fallback(settings):
    settings.OIDC_RP_CLIENT_ID = "client"
    settings.OIDC_RP_CLIENT_SECRET = "secret"
    make_user()
    backend = CRMOIDCBackend()

    unlinked_claims = claims(preferred_username="i_vovani")
    unlinked_claims.pop(CRM_TELEGRAM_ID_CLAIM)
    assert not backend.filter_users_by_claims(unlinked_claims).exists()


def test_create_user_preserves_actionable_binding_failure(settings, rf):
    settings.OIDC_RP_CLIENT_ID = "client"
    settings.OIDC_RP_CLIENT_SECRET = "secret"
    make_user()
    request = rf.get("/oidc/callback/")
    from django.contrib.sessions.middleware import SessionMiddleware

    SessionMiddleware(lambda _request: None).process_request(request)
    request.session.save()
    backend = CRMOIDCBackend()
    backend.request = request
    unlinked_claims = claims()
    unlinked_claims.pop(CRM_TELEGRAM_ID_CLAIM)

    assert not backend.filter_users_by_claims(unlinked_claims).exists()
    with pytest.raises(SuspiciousOperation, match="not linked"):
        backend.create_user(unlinked_claims)
    assert request.session[OIDC_ERROR_SESSION_KEY] == "link_missing"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" 10001 ", 10001), (10001, 10001), ("", None), ("@10001", None), ("-1", None), (True, None)],
)
def test_explicit_telegram_id_claim_is_normalized(raw, expected):
    assert normalize_telegram_id(raw) == expected


def test_explicit_telegram_id_claim_matches_exact_user(settings):
    settings.OIDC_RP_CLIENT_ID = "client"
    settings.OIDC_RP_CLIENT_SECRET = "secret"
    user = make_user(telegram_username="Mixed_Case")
    backend = CRMOIDCBackend()

    matched = backend.filter_users_by_claims(
        claims(**{CRM_TELEGRAM_ID_CLAIM: "  10001  "})
    )
    assert list(matched) == [user]


def test_blocked_identity_is_not_bound(settings):
    settings.OIDC_RP_CLIENT_ID = "client"
    settings.OIDC_RP_CLIENT_SECRET = "secret"
    make_user(oidc_binding_blocked=True)
    backend = CRMOIDCBackend()
    assert not backend.filter_users_by_claims(
        claims(**{CRM_TELEGRAM_ID_CLAIM: "10001"})
    ).exists()


def test_username_match_cannot_bind_without_matching_telegram_id(settings):
    settings.OIDC_RP_CLIENT_ID = "client"
    settings.OIDC_RP_CLIENT_SECRET = "secret"
    make_user(telegram_username="i_vovani")
    backend = CRMOIDCBackend()

    assert not backend.filter_users_by_claims(
        claims(**{CRM_TELEGRAM_ID_CLAIM: "10002"})
    ).exists()


def test_stale_candidate_cannot_be_rebound_to_another_subject(settings):
    settings.OIDC_RP_CLIENT_ID = "client"
    settings.OIDC_RP_CLIENT_SECRET = "secret"
    user = make_user()
    backend = CRMOIDCBackend()
    user.oidc_subject = "first-subject"
    user.save(update_fields=["oidc_subject"])

    with pytest.raises(SuspiciousOperation, match="already bound"):
        backend.update_user(user, claims(sub="second-subject"))


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


def test_oidc_failure_renders_actionable_page_instead_of_redirect_loop(client):
    session = client.session
    session[OIDC_ERROR_SESSION_KEY] = "link_missing"
    session.save()

    response = client.get(reverse("crm:login"), {"error": "oidc"})
    assert response.status_code == 403
    assert "ещё не связана" in response.content.decode()
    assert OIDC_ERROR_SESSION_KEY not in client.session


def test_terminate_user_sessions_revokes_only_target_user(client):
    target = make_user()
    other = make_user(
        telegram_id=10002,
        username="tg_10002",
        telegram_username="another_user",
    )

    client.force_login(target, backend="django.contrib.auth.backends.ModelBackend")
    target_key = client.session.session_key

    from django.test import Client

    other_client = Client()
    other_client.force_login(other, backend="django.contrib.auth.backends.ModelBackend")
    other_key = other_client.session.session_key

    assert terminate_user_sessions(target.pk) == 1
    from django.contrib.sessions.models import Session

    assert not Session.objects.filter(session_key=target_key).exists()
    assert Session.objects.filter(session_key=other_key).exists()


def test_admin_unlink_blocks_rebinding_and_revokes_sessions(client):
    admin = make_user(oidc_subject="admin-subject")
    target = make_user(
        telegram_id=10002,
        username="tg_10002",
        telegram_username="worker",
        role=UserRole.WORKER,
        oidc_subject="worker-subject",
    )

    from django.test import Client
    from django.contrib.sessions.models import Session

    target_client = Client()
    target_client.force_login(target, backend="django.contrib.auth.backends.ModelBackend")
    target_key = target_client.session.session_key

    client.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")
    response = client.post(
        reverse("control:user_edit", args=[target.pk]),
        {"action": "unlink_oidc", "next": "control:users"},
    )
    assert response.status_code == 302
    target.refresh_from_db()
    assert target.oidc_subject is None
    assert target.oidc_binding_blocked is True
    assert not Session.objects.filter(session_key=target_key).exists()

    client.post(
        reverse("control:user_edit", args=[target.pk]),
        {"action": "allow_oidc_rebind", "next": "control:users"},
    )
    target.refresh_from_db()
    assert target.oidc_binding_blocked is False


def test_admin_cannot_unlink_current_identity(client):
    admin = make_user(oidc_subject="admin-subject")
    client.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")

    response = client.post(
        reverse("control:user_edit", args=[admin.pk]),
        {"action": "unlink_oidc", "next": "control:users"},
    )
    assert response.status_code == 302
    admin.refresh_from_db()
    assert admin.oidc_subject == "admin-subject"
    assert admin.oidc_binding_blocked is False
