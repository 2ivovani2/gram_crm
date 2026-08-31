from io import StringIO

import pytest
from django.core.management import call_command

from apps.crm.models import CRMRole, Workspace, WorkspaceMembership
from apps.crm.provisioning import ensure_default_workspace_membership
from apps.users.models import User, UserRole, UserStatus


pytestmark = pytest.mark.django_db


def make_user(telegram_id: int, role: str, **overrides) -> User:
    values = {
        "telegram_id": telegram_id,
        "username": f"tg_{telegram_id}",
        "role": role,
        "status": UserStatus.ACTIVE,
        "is_active": True,
        "oidc_subject": f"subject-{telegram_id}",
    }
    values.update(overrides)
    return User.objects.create(**values)


@pytest.fixture
def workspace():
    return Workspace.objects.create(name="Gramly", slug="gramly", is_active=True)


@pytest.mark.parametrize(
    ("user_role", "workspace_role"),
    [
        (UserRole.ADMIN, CRMRole.OWNER),
        (UserRole.ACCOUNTANT, CRMRole.FINANCE),
        (UserRole.CURATOR, CRMRole.VIEWER),
        (UserRole.WORKER, CRMRole.VIEWER),
    ],
)
def test_oidc_role_matrix_creates_safe_membership(workspace, user_role, workspace_role):
    user = make_user(20000 + len(User.objects.all()), user_role)

    first = ensure_default_workspace_membership(user)
    second = ensure_default_workspace_membership(user)

    assert first.action == "created"
    assert second.action == "exists"
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    assert membership.role == workspace_role
    assert membership.is_active is True
    assert WorkspaceMembership.objects.filter(workspace=workspace, user=user).count() == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"role": UserRole.ANONYMOUS},
        {"status": UserStatus.INACTIVE},
        {"status": UserStatus.BANNED},
        {"is_active": False},
        {"oidc_subject": None},
    ],
)
def test_ineligible_users_are_not_provisioned(workspace, overrides):
    role = overrides.get("role", UserRole.WORKER)
    user = make_user(
        21000 + len(User.objects.all()),
        role,
        **{key: value for key, value in overrides.items() if key != "role"},
    )

    result = ensure_default_workspace_membership(user)

    assert result.action.startswith("skipped_")
    assert not WorkspaceMembership.objects.filter(workspace=workspace, user=user).exists()


def test_revoked_membership_is_never_reactivated(workspace):
    user = make_user(22001, UserRole.CURATOR)
    membership = WorkspaceMembership.objects.create(
        workspace=workspace,
        user=user,
        role=CRMRole.VIEWER,
        is_active=False,
    )

    result = ensure_default_workspace_membership(user)

    membership.refresh_from_db()
    assert result.action == "revoked"
    assert membership.is_active is False


@pytest.mark.parametrize("explicit_role", [CRMRole.OWNER, CRMRole.FINANCE, CRMRole.APPLICATIONS])
def test_existing_explicit_role_is_preserved(workspace, explicit_role):
    user = make_user(23000 + len(User.objects.all()), UserRole.WORKER)
    membership = WorkspaceMembership.objects.create(
        workspace=workspace,
        user=user,
        role=explicit_role,
        is_active=True,
    )

    result = ensure_default_workspace_membership(user)

    membership.refresh_from_db()
    assert result.action == "exists"
    assert membership.role == explicit_role


def test_reconciliation_dry_run_and_apply_are_idempotent(workspace):
    user = make_user(24001, UserRole.CURATOR)
    dry_output = StringIO()
    call_command(
        "reconcile_crm_memberships",
        "--dry-run",
        "--telegram-id",
        str(user.telegram_id),
        stdout=dry_output,
    )
    assert "action=create" in dry_output.getvalue()
    assert not WorkspaceMembership.objects.filter(user=user).exists()

    apply_output = StringIO()
    call_command(
        "reconcile_crm_memberships",
        "--apply",
        "--telegram-id",
        str(user.telegram_id),
        stdout=apply_output,
    )
    assert "action=created" in apply_output.getvalue()

    second_output = StringIO()
    call_command(
        "reconcile_crm_memberships",
        "--apply",
        "--telegram-id",
        str(user.telegram_id),
        stdout=second_output,
    )
    assert "action=exists" in second_output.getvalue()
    assert WorkspaceMembership.objects.filter(user=user).count() == 1
