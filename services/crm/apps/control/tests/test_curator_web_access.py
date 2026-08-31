import pytest
from django.urls import reverse

from apps.crm.models import CRMRole, Workspace, WorkspaceMembership
from apps.users.models import User, UserRole, UserStatus


pytestmark = pytest.mark.django_db


def make_user(telegram_id: int, role: str) -> User:
    return User.objects.create_user(
        telegram_id=telegram_id,
        username=f"user-{telegram_id}",
        telegram_username=f"user_{telegram_id}",
        role=role,
        status=UserStatus.ACTIVE,
    )


@pytest.fixture
def workspace():
    return Workspace.objects.create(name="Gramly", slug="gramly", is_active=True)


def add_member(workspace, user, role):
    return WorkspaceMembership.objects.create(
        workspace=workspace,
        user=user,
        role=role,
        is_active=True,
    )


def test_curator_lands_on_moderation_dashboard_and_sees_only_moderation_nav(client, workspace):
    curator = make_user(31001, UserRole.CURATOR)
    add_member(workspace, curator, CRMRole.VIEWER)
    client.force_login(curator)

    landing = client.get(reverse("crm:dashboard"))
    assert landing.status_code == 302
    assert landing.url == reverse("control:dashboard")

    dashboard = client.get(reverse("control:dashboard"))
    content = dashboard.content.decode()
    assert dashboard.status_code == 200
    assert "Отчёты" in content
    assert "Шаблоны" in content
    assert "Штрафы" not in content
    assert "Сотрудники" not in content
    assert "Пользователи" not in content
    assert "Выводы" not in content
    assert "Настройки" not in content


@pytest.mark.parametrize(
    "url_name",
    [
        "control:penalties",
        "control:employees",
        "control:withdrawals",
        "control:analytics",
        "control:users",
        "control:deadline_notifications",
        "control:settings",
    ],
)
def test_curator_is_forbidden_from_administrative_control_pages(client, workspace, url_name):
    curator = make_user(32000 + len(User.objects.all()), UserRole.CURATOR)
    add_member(workspace, curator, CRMRole.VIEWER)
    client.force_login(curator)

    assert client.get(reverse(url_name)).status_code == 403


@pytest.mark.parametrize("url_name", ["control:dashboard", "control:reports", "control:report_templates"])
def test_curator_can_open_moderation_pages(client, workspace, url_name):
    curator = make_user(33000 + len(User.objects.all()), UserRole.CURATOR)
    add_member(workspace, curator, CRMRole.VIEWER)
    client.force_login(curator)

    assert client.get(reverse(url_name)).status_code == 200


def test_worker_cannot_open_control_dashboard(client, workspace):
    worker = make_user(34001, UserRole.WORKER)
    add_member(workspace, worker, CRMRole.VIEWER)
    client.force_login(worker)

    assert client.get(reverse("control:dashboard")).status_code == 403


def test_user_management_shows_sso_and_workspace_access_states(client, workspace):
    admin = make_user(35001, UserRole.ADMIN)
    add_member(workspace, admin, CRMRole.OWNER)
    available = make_user(35002, UserRole.WORKER)
    available.oidc_subject = "available-subject"
    available.save(update_fields=["oidc_subject"])
    add_member(workspace, available, CRMRole.VIEWER)
    missing = make_user(35003, UserRole.WORKER)
    missing.oidc_subject = "missing-subject"
    missing.save(update_fields=["oidc_subject"])
    revoked = make_user(35004, UserRole.WORKER)
    revoked.oidc_subject = "revoked-subject"
    revoked.save(update_fields=["oidc_subject"])
    membership = add_member(workspace, revoked, CRMRole.VIEWER)
    membership.is_active = False
    membership.save(update_fields=["is_active"])
    make_user(35005, UserRole.WORKER)
    client.force_login(admin)

    response = client.get(reverse("control:users"), {"role": "", "status": "all"})
    content = response.content.decode()

    assert response.status_code == 200
    assert "SSO привязан" in content
    assert "Workspace доступен" in content
    assert "Нет workspace-доступа" in content
    assert "Доступ отозван" in content
    assert "Ожидает первого SSO-входа" in content
