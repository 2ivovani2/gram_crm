"""Local, DB/network-free tests for web authorization boundaries."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from apps.control import views as control
from apps.crm import views as crm
from apps.crm.access import Access
from apps.owners import views as owners
from apps.users.models import User, UserRole, UserStatus


@pytest.mark.parametrize("view,read,write", [
    (control.ControlDashboardView, "dashboard.view", "dashboard.view"),
    (control.ReportTemplatesView, "templates.view", "templates.manage"),
    (control.ReportTemplateEditView, "templates.view", "templates.manage"),
    (control.ReportsListView, "reports.view", "reports.view"),
    (control.ReportDetailView, "reports.view", "reports.moderate"),
    (control.ReportMediaView, "reports.view", "reports.view"),
    (control.PenaltiesListView, "penalties.view", "penalties.create"),
    (control.PenaltyActionView, "penalties.resolve", "penalties.resolve"),
    (control.EmployeesView, "employees.view", "employees.view"),
    (control.EmployeeKPIView, "employees.view", "employees.manage"),
    (control.WithdrawalsListView, "withdrawals.view", "withdrawals.view"),
    (control.WithdrawalActionView, "withdrawals.manage", "withdrawals.manage"),
    (control.AnalyticsView, "analytics.view", "analytics.view"),
    (control.UsersListView, "employees.view", "employees.manage"),
    (control.UserEditView, "access.manage", "access.manage"),
    (control.DeadlineNotificationsView, "notifications.view", "notifications.manage"),
    (control.ControlSettingsView, "settings.manage", "settings.manage"),
    (crm.FinanceEntryView, "finance.view", "finance.manage"),
    (crm.ApplicationEntryView, "finance.view", "finance.manage"),
    (crm.HistoryView, "analytics.view", "analytics.view"),
    (crm.DayDetailView, "analytics.view", "analytics.view"),
    (crm.ReportDetailView, "analytics.view", "analytics.view"),
    (crm.AdminIndexView, "analytics.view", "analytics.view"),
    (crm.AdminMembersView, "access.manage", "access.manage"),
    (crm.GenerateReportView, "finance.manage", "finance.manage"),
    (crm.ExportView, "analytics.export", "analytics.export"),
    (crm.AdSlotCalculatorView, "calculator.use", "calculator.use"),
    (owners.OwnersDashboardView, "owners.view", "owners.manage"),
    (owners.OwnerDetailView, "owners.view", "owners.manage"),
    (owners.OwnerCreateView, "owners.manage", "owners.manage"),
    (owners.OwnerEditView, "owners.manage", "owners.manage"),
    (owners.OwnerActionView, "owners.manage", "owners.manage"),
    (owners.OwnerChannelCreateView, "owners.manage", "owners.manage"),
    (owners.OwnerChannelActionView, "owners.manage", "owners.manage"),
    (owners.OwnerActivityView, "owners.view", "owners.manage"),
    (owners.OwnerStatusesView, "owners.view", "owners.manage"),
    (owners.OwnerStatusActionView, "owners.manage", "owners.manage"),
    (owners.SavedFilterActionView, "owners.view", "owners.view"),
])
def test_method_permission_contract(view, read, write):
    instance = view()
    rf = RequestFactory()
    assert instance.get_required_permission(rf.get("/")) == read
    assert instance.get_required_permission(rf.post("/")) == write


@pytest.mark.parametrize("action", ["update_finances", "save_kpi", "edit_balance"])
def test_legacy_financial_actions_require_employee_finance(action):
    request = RequestFactory().post("/", {"action": action})
    assert control.EmployeeKPIView().get_required_permission(request) == "employees.finance"


@pytest.mark.parametrize("role", list(UserRole.values))
def test_denied_capability_cannot_be_bypassed_by_business_or_workspace_owner(role):
    user = User(pk=1, role=role, status=UserStatus.ACTIVE)
    request = RequestFactory().post("/", {"action": "accept"})
    request.crm_user = user
    request.crm_workspace = SimpleNamespace(pk=1, slug="gramly")
    request.crm_is_owner = True
    view = control.ReportDetailView()
    view.get_crm_context = Mock(return_value={})
    with patch("apps.crm.access.has_permission", return_value=False) as check, patch(
        "apps.crm.views.render", return_value=HttpResponse(status=403)
    ):
        assert view.check_crm_permissions(request).status_code == 403
    check.assert_called_once_with(user, "reports.moderate", request.crm_workspace)


@pytest.mark.parametrize("membership_active,status,allowed", [
    (False, UserStatus.ACTIVE, {"access.manage"}),
    (True, UserStatus.INACTIVE, {"access.manage"}),
    (True, UserStatus.BANNED, {"access.manage"}),
    (True, UserStatus.ACTIVE, set()),
])
def test_login_hardgate_runs_before_view(membership_active, status, allowed):
    user = User(pk=1, role=UserRole.ADMIN, status=status)
    request = RequestFactory().get("/")
    request.user = user
    request.session = {}
    workspace = SimpleNamespace(pk=1, slug="gramly")
    membership = SimpleNamespace(is_active=membership_active)
    with patch.object(User.objects, "get", return_value=user), patch.object(
        crm.CRMLoginMixin, "_resolve_workspace_and_membership", return_value=(workspace, membership)
    ), patch("apps.crm.access.get_access", return_value=Access(allowed=frozenset(allowed))), patch.object(
        control.ControlDashboardView, "get", side_effect=AssertionError("view must not run")
    ):
        response = control.ControlDashboardView.as_view()(request)
    assert response.status_code == 403


def test_template_financial_override_is_rejected_before_any_update():
    template = SimpleNamespace(auto_penalty_amount=Decimal("500"), delete=Mock(), save=Mock())
    request = RequestFactory().post("/", {"action": "save", "auto_penalty_amount": "1000"})
    request.crm_user = SimpleNamespace(pk=1)
    request.crm_workspace = SimpleNamespace(pk=1)
    with patch("apps.control.views.get_object_or_404", return_value=template), patch(
        "apps.control.views.has_permission", return_value=False
    ):
        response = control.ReportTemplateEditView.post.__wrapped__(control.ReportTemplateEditView(), request, pk=1)
    assert response.status_code == 403
    assert template.auto_penalty_amount == Decimal("500")
    template.save.assert_not_called()


def test_template_create_financial_override_does_not_create_partial_template():
    from apps.control.models import ReportTemplate
    request = RequestFactory().post("/", {"action": "create", "name": "Test", "auto_penalty_amount": "10"})
    request.crm_user = SimpleNamespace(pk=1)
    request.crm_workspace = SimpleNamespace(pk=1)
    with patch("apps.control.views.has_permission", return_value=False), patch.object(
        ReportTemplate.objects, "create"
    ) as create:
        response = control.ReportTemplatesView().post(request)
    assert response.status_code == 403
    create.assert_not_called()


@pytest.mark.parametrize("action", ["set_role", "set_crm_role"])
def test_legacy_role_mutation_redirects_without_saving(action):
    user = SimpleNamespace(pk=7, save=Mock())
    request = RequestFactory().post("/", {"action": action, "role": "admin", "crm_role": "owner"})
    request.crm_user = SimpleNamespace(pk=1)
    request.crm_workspace = SimpleNamespace(pk=1)
    with patch("apps.control.views.get_object_or_404", return_value=user), patch(
        "apps.control.views.messages.info"
    ), patch("apps.control.views.redirect", return_value=HttpResponse(status=302)) as redirect:
        response = control.UserEditView().post(request, pk=7)
    assert response.status_code == 302
    redirect.assert_called_once_with("control:user_access", pk=7)
    user.save.assert_not_called()


def test_permission_is_rechecked_after_revocation_not_cached_in_view():
    request = RequestFactory().post("/", {"action": "create"})
    request.crm_user = SimpleNamespace(pk=1)
    request.crm_workspace = SimpleNamespace(pk=1)
    view = control.PenaltiesListView()
    view.get_crm_context = Mock(return_value={})
    with patch("apps.crm.access.has_permission", side_effect=[True, False]) as check, patch(
        "apps.crm.views.render", return_value=HttpResponse(status=403)
    ):
        assert view.check_crm_permissions(request) is None
        assert view.check_crm_permissions(request).status_code == 403
    assert check.call_count == 2


@pytest.mark.parametrize("action,status", [("set_status", UserStatus.BANNED), ("delete", UserStatus.INACTIVE)])
def test_status_changes_use_protected_service_and_terminate_sessions(action, status):
    user = SimpleNamespace(pk=7, save=Mock(), status=UserStatus.ACTIVE)
    request = RequestFactory().post("/", {"action": action, "status": status})
    request.crm_user = SimpleNamespace(pk=1)
    request.crm_workspace = SimpleNamespace(pk=1)
    with patch("apps.control.views.get_object_or_404", return_value=user), patch(
        "apps.crm.access.update_user_status"
    ) as update, patch("apps.crm.identity.terminate_user_sessions") as terminate, patch(
        "apps.control.views.messages.success"
    ), patch("apps.control.views.redirect", return_value=HttpResponse(status=302)):
        assert control.UserEditView().post(request, pk=7).status_code == 302
    update.assert_called_once_with(request.crm_user, user, status, request.crm_workspace)
    terminate.assert_called_once_with(7)
    user.save.assert_not_called()


def test_legacy_remove_membership_uses_protected_service():
    user = SimpleNamespace(pk=7)
    request = RequestFactory().post("/", {"action": "remove_crm_role"})
    request.crm_user = SimpleNamespace(pk=1)
    request.crm_workspace = SimpleNamespace(pk=1)
    with patch("apps.control.views.get_object_or_404", return_value=user), patch(
        "apps.crm.access.revoke_access"
    ) as revoke, patch("apps.crm.identity.terminate_user_sessions") as terminate, patch(
        "apps.control.views.messages.success"
    ), patch("apps.control.views.redirect", return_value=HttpResponse(status=302)):
        assert control.UserEditView().post(request, pk=7).status_code == 302
    revoke.assert_called_once_with(request.crm_user, user, request.crm_workspace)
    terminate.assert_called_once_with(7)
