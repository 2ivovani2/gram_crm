import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.urls import reverse

from apps.crm.access import (
    ROLE_PERMISSIONS, StaleAccess, effective_permissions, get_access, has_permission,
    normalize_overrides, revoke_access, update_access, update_user_status,
)
from apps.crm.models import AccessChange, Workspace, WorkspaceMembership
from apps.crm.provisioning import ensure_default_workspace_membership
from apps.users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def workspace():
    return Workspace.objects.create(name="Local test", slug="gramly")


def person(workspace, role="worker", **kwargs):
    uid = 870000 + User.objects.count()
    user = User.objects.create_user(username=f"local-{uid}", telegram_id=uid, role=role, status="active", **kwargs)
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role={"admin": "owner", "accountant": "finance"}.get(role, "viewer"))
    return user


@pytest.mark.parametrize("role", ROLE_PERMISSIONS)
def test_default_matrix(workspace, role):
    user = person(workspace, role)
    assert get_access(user).allowed == ROLE_PERMISSIONS[role]


@pytest.mark.parametrize("status", ["inactive", "banned", "pending"])
def test_status_is_hard_gate_even_for_admin_with_stale_user(workspace, status):
    user = person(workspace, "admin")
    User.objects.filter(pk=user.pk).update(status=status)
    assert not has_permission(user, "access.manage")


def test_revocation_workspace_deactivation_and_legacy_owner_no_bypass(workspace):
    user = person(workspace)
    WorkspaceMembership.objects.filter(user=user).update(role="owner")
    assert not has_permission(user, "access.manage")
    assert not has_permission(user, "finance.manage")
    WorkspaceMembership.objects.filter(user=user).update(is_active=False)
    assert not get_access(user).allowed
    WorkspaceMembership.objects.filter(user=user).update(is_active=True)
    workspace.is_active = False
    workspace.save()
    assert not get_access(user).allowed


def test_dependencies_and_management_not_assignable():
    assert "reports.view" in effective_permissions("worker", {"reports.moderate": True})
    assert "reports.moderate" not in effective_permissions("curator", {"reports.view": False})
    assert "templates.financial" not in effective_permissions("admin", {"templates.view": False, "templates.financial": True})
    for value in ({"access.manage": True}, {"access.manage": False}, {"unknown": True}, {"reports.view": 1}):
        with pytest.raises(ValidationError):
            normalize_overrides("worker", value)


def test_access_update_version_reset_and_audit(workspace):
    actor, target = person(workspace, "admin"), person(workspace)
    membership = update_access(actor, target, "worker", {"reports.view": True}, 0)
    assert membership.access_version == 1
    assert has_permission(target, "reports.view")
    assert AccessChange.objects.count() == 1
    with pytest.raises(StaleAccess):
        update_access(actor, target, "worker", {}, 0)
    with pytest.raises(ValidationError):
        update_access(actor, target, "curator", {}, 1)
    with pytest.raises(ValidationError):
        update_access(actor, target, "curator", {"reports.view": True}, 1, confirm_role_change=True)
    update_access(actor, target, "curator", {}, 1, confirm_role_change=True)
    assert get_access(target).role == "curator"
    assert get_access(target).version == 2
    assert has_permission(target, "penalties.create")
    assert not has_permission(target, "penalties.resolve")
    assert not has_permission(target, "templates.financial")


def test_noop_and_rollback_do_not_create_audit_or_grants(workspace):
    actor, target = person(workspace, "admin"), person(workspace)
    update_access(actor, target, "worker", {}, 0)
    assert not AccessChange.objects.exists()
    with pytest.raises(RuntimeError), transaction.atomic():
        update_access(actor, target, "worker", {"finance.manage": True}, 0)
        raise RuntimeError("rollback test")
    assert not AccessChange.objects.exists()
    assert not has_permission(target, "finance.view")


def test_self_and_non_admin_cannot_manage_access(workspace):
    actor, target = person(workspace, "admin"), person(workspace, "curator")
    with pytest.raises(PermissionDenied):
        update_access(actor, actor, "worker", {}, 0, confirm_role_change=True)
    with pytest.raises(PermissionDenied):
        update_user_status(actor, actor, "inactive")
    with pytest.raises(PermissionDenied):
        revoke_access(actor, actor)
    with pytest.raises(PermissionDenied):
        update_access(target, actor, "worker", {}, 0, confirm_role_change=True)


def test_any_active_admin_can_assign_admin_and_revoked_admin_cannot(workspace):
    actor, target = person(workspace, "admin"), person(workspace)
    update_access(actor, target, "admin", {}, 0, confirm_role_change=True)
    assert has_permission(target, "access.manage")
    revoke_access(actor, target)
    with pytest.raises(PermissionDenied):
        update_user_status(target, actor, "inactive")


def test_enroll_before_sso_and_no_automatic_reactivation(workspace):
    user = User.objects.create_user(username="new-local", telegram_id=890001, role="curator", status="active")
    assert not user.oidc_subject
    assert ensure_default_workspace_membership(user).action == "created"
    assert ensure_default_workspace_membership(user).action == "exists"
    WorkspaceMembership.objects.filter(user=user).update(is_active=False)
    assert ensure_default_workspace_membership(user).action == "revoked"
    assert not get_access(user).allowed


def test_reconciliation_dry_run_and_ambiguous_is_all_or_nothing(workspace):
    missing = User.objects.create_user(username="pending-membership", telegram_id=890002, role="worker", status="active")
    call_command("reconcile_crm_memberships", dry_run=True)
    assert not WorkspaceMembership.objects.filter(user=missing).exists()
    odd = person(workspace, "worker")
    WorkspaceMembership.objects.filter(user=odd).update(role="owner")
    with pytest.raises(CommandError):
        call_command("reconcile_crm_memberships", apply=True)
    assert not WorkspaceMembership.objects.filter(user=missing).exists()
    WorkspaceMembership.objects.filter(user=odd).update(role="viewer")
    call_command("reconcile_crm_memberships", apply=True)
    assert WorkspaceMembership.objects.filter(user=missing).exists()


def form_data(role="worker", version=0):
    from apps.control.access_views import AccessForm
    return {**{code: "role" for code in AccessForm().fields if code not in {"role", "version"}}, "role": role, "version": version}


def test_form_requires_signed_preview_and_stale_confirmation_is_rejected(client, workspace):
    actor, target = person(workspace, "admin"), person(workspace)
    client.force_login(actor)
    url = reverse("control:user_access", args=[target.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert "Authentik отвечает за вход" in response.content.decode()
    preview = client.post(url, {**form_data("curator"), "action": "preview"})
    assert preview.status_code == 200
    assert not AccessChange.objects.exists()
    token = preview.context["preview"]["confirmation"]
    update_access(actor, target, "worker", {"reports.view": True}, 0)
    assert client.post(url, {"action": "save", "confirmation": token}).status_code == 409
    assert client.post(url, {"action": "save", "confirmation": "forged"}).status_code == 400


def test_form_confirmation_and_existing_ssO_are_independent(client, workspace):
    actor, target = person(workspace, "admin"), person(workspace)
    client.force_login(actor)
    url = reverse("control:user_access", args=[target.pk])
    preview = client.post(url, {**form_data("curator"), "action": "preview"})
    token = preview.context["preview"]["confirmation"]
    assert client.post(url, {"action": "save", "confirmation": token}).status_code == 302
    target.refresh_from_db()
    assert target.role == "curator"
    assert not target.oidc_subject
    assert has_permission(target, "reports.moderate")
    assert client.get(reverse("control:user_access", args=[actor.pk])).status_code == 403


def test_missing_actor_member_no_admin_fallback(workspace):
    actor = person(workspace, "admin")
    target = person(workspace)
    WorkspaceMembership.objects.filter(user=actor).delete()
    with pytest.raises(PermissionDenied):
        update_access(actor, target, "admin", {}, 0, confirm_role_change=True)


def test_personal_landing_remains_available_without_hr_permission(client, workspace):
    worker = person(workspace)
    client.force_login(worker)
    assert client.get(reverse("crm:dashboard")).status_code == 200
    assert client.get(reverse("control:dashboard")).status_code == 403
    assert client.get(reverse("control:reports")).status_code == 403
    assert client.get(reverse("crm:entry_finance")).status_code == 403


def test_admin_access_management_remains_reachable_if_employee_view_denied(client, workspace):
    admin = person(workspace, "admin")
    WorkspaceMembership.objects.filter(user=admin).update(permission_overrides={"employees.view": False})
    client.force_login(admin)
    assert client.get(reverse("control:users")).status_code == 200
    assert client.get(reverse("control:employees")).status_code == 403


def test_last_admin_check_is_serialized_under_workspace_lock(workspace):
    # The workspace row lock serializes both competing admin demotions on PG.
    actor, other = person(workspace, "admin"), person(workspace, "admin")
    update_access(actor, other, "worker", {}, 0, confirm_role_change=True)
    with pytest.raises(PermissionDenied):
        update_access(other, actor, "worker", {}, 0, confirm_role_change=True)
    assert has_permission(actor, "access.manage")


def test_oidc_does_not_call_provisioning():
    from apps.crm.auth import CRMOIDCBackend
    import inspect
    assert "ensure_default_workspace_membership" not in inspect.getsource(CRMOIDCBackend.update_user)


@pytest.mark.parametrize("method", ["save_finance_entry", "save_application_entry"])
def test_financial_entry_service_rechecks_revocation(workspace, method):
    from datetime import date
    from apps.crm.services import EntryService
    actor = person(workspace, "accountant")
    WorkspaceMembership.objects.filter(user=actor).update(permission_overrides={"finance.manage": False})
    with pytest.raises(PermissionDenied):
        getattr(EntryService, method)(workspace, date(2026, 9, 10), actor, {})
