"""
CRM web views.

URL prefix: /crm/

Auth: Authentik OIDC → Django session. Only an existing active CRM user whose
      Telegram ID matches the explicit Authentik claim can authenticate.

Access is resolved from current CRM role, capability overrides and active
workspace membership. Authentik authenticates; it does not grant CRM rights.

Mixins:
  CRMLoginMixin       — authentication and active user/workspace hard gates
  CRMPermissionMixin  — explicit per-method capability requirements

The permission check runs BEFORE the view method via check_crm_permissions()
hook — CRMLoginMixin.dispatch() calls it after setting up request attributes
but before delegating to super().dispatch() (which routes to get/post).
"""
from __future__ import annotations

import datetime
import logging
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.http import HttpResponse, Http404, HttpResponseForbidden
from django.shortcuts import redirect, render, get_object_or_404
from django.views import View
from django.views.generic import TemplateView

logger = logging.getLogger(__name__)
_MSK = ZoneInfo("Europe/Moscow")


# ─── Mixins ───────────────────────────────────────────────────────────────────

class CRMLoginMixin:
    """
    Require CRM session (any authenticated DB user).

    Sets on request:
      crm_user        — User instance
      crm_workspace   — Workspace or None
      crm_membership  — WorkspaceMembership or None
      crm_is_owner    — compatibility display flag for access.manage
      crm_access      — fresh capability snapshot, never a session-level grant
    """

    def dispatch(self, request, *args, **kwargs):
        from django.conf import settings as _s
        _login_url = f"https://crm.{getattr(_s, 'DOMAIN', 'gramly.tech')}/crm/login/"

        # 1. OIDC-backed Django session check
        if not request.user.is_authenticated:
            return redirect(_login_url)

        # 2. Load the current CRM user
        from apps.users.models import User
        if not isinstance(request.user, User):
            return redirect(_login_url)
        # Session identity is not an authorization snapshot. Role/status changes
        # apply on the next request, including an already open browser tab.
        request.crm_user = User.objects.get(pk=request.user.pk)

        # 3. Resolve workspace + membership
        workspace, membership = self._resolve_workspace_and_membership(request)
        request.crm_workspace = workspace
        request.crm_membership = membership

        from apps.crm.access import get_access
        from apps.users.models import UserStatus, UserRole
        request.crm_access = get_access(request.crm_user, workspace=workspace)
        request.crm_is_owner = request.crm_access.allows("access.manage")
        if (
            workspace is None
            or membership is None or not membership.is_active
            or request.crm_user.status != UserStatus.ACTIVE
            or request.crm_user.role == UserRole.ANONYMOUS
            or not request.crm_access.allowed
        ):
            return HttpResponseForbidden("Доступ к рабочему пространству отозван или не предоставлен.")

        # 5. Permission hook — subclasses return a response to deny, None to allow
        denial = self.check_crm_permissions(request)
        if denial is not None:
            return denial

        # 6. Run the actual view method
        return super().dispatch(request, *args, **kwargs)

    def _resolve_workspace_and_membership(self, request):
        """
        Resolve the selected workspace without silently restoring revoked access.
        """
        from apps.crm.models import WorkspaceMembership, Workspace

        workspace_id = request.session.get("active_workspace_id")
        workspace = (
            Workspace.objects.filter(pk=workspace_id).first()
            if workspace_id else Workspace.objects.filter(slug="gramly").first()
        )
        if workspace is None:
            return None, None
        membership = WorkspaceMembership.objects.filter(
            user=request.crm_user, workspace=workspace,
        ).first()
        request.session["active_workspace_id"] = workspace.pk
        return workspace, membership

    def check_crm_permissions(self, request):
        """
        Override in subclasses to add permission checks.
        Return an HttpResponse to deny access, or None to allow.
        Called BEFORE the view method runs.
        """
        return None

    def get_crm_context(self, request):
        from apps.crm.models import WorkspaceMembership
        all_memberships = list(
            WorkspaceMembership.objects.filter(user=request.crm_user, is_active=True)
            .select_related("workspace")
            .order_by("workspace__name")
        )
        m = request.crm_membership
        user = request.crm_user

        from apps.crm.access import permission_context
        permissions = permission_context(user, request.crm_workspace)
        can_finance = request.crm_access.allows("finance.view")
        can_apps = request.crm_access.allows("finance.view")

        # Role label: prefer bot role for clarity
        role_label = user.get_role_display() if hasattr(user, "get_role_display") else (
            m.get_role_display() if m else None
        )

        return {
            **permissions,
            "permissions": permissions,
            "crm_user":               user,
            "workspace":              request.crm_workspace,
            "membership":             m,
            "all_workspaces":         [ms.workspace for ms in all_memberships],
            "all_memberships":        all_memberships,
            "is_owner":               request.crm_is_owner,
            "can_enter_finance":      can_finance,
            "can_enter_applications": can_apps,
            "crm_role":               role_label,
        }


class CRMPermissionMixin(CRMLoginMixin):
    """Explicit capabilities; workspace roles never grant an authorization bypass."""

    required_permission = "access.manage"
    write_permission = None

    def get_required_permission(self, request):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and self.write_permission:
            return self.write_permission
        return self.required_permission

    def check_crm_permissions(self, request):
        from apps.crm.access import has_permission
        if not has_permission(request.crm_user, self.get_required_permission(request), request.crm_workspace):
            return render(request, "crm/403.html", self.get_crm_context(request), status=403)
        return None


class CRMOwnerMixin(CRMPermissionMixin):
    """Compatibility name: subclasses must specify their capability."""


class CRMFinanceMixin(CRMPermissionMixin):
    required_permission = "finance.view"
    write_permission = "finance.manage"


class CRMApplicationsMixin(CRMFinanceMixin):
    """Operational financial inputs use the same capability boundary."""


# ─── Auth views ───────────────────────────────────────────────────────────────

class LoginView(TemplateView):
    template_name = "crm/login.html"

    ERROR_MESSAGES = {
        "link_missing": (
            "Ваша рабочая учётная запись ещё не связана с Telegram-профилем CRM. "
            "Попросите администратора заполнить поле Telegram ID в Authentik."
        ),
        "link_not_found": (
            "Указанный в Authentik Telegram ID не найден в CRM. "
            "Проверьте ID и сначала запустите CRM-бота Gramly."
        ),
        "link_ambiguous": (
            "CRM обнаружила конфликт Telegram ID. "
            "Администратору необходимо проверить данные сотрудника."
        ),
        "link_occupied": "Этот Telegram-профиль уже связан с другой SSO-учётной записью.",
        "link_blocked": (
            "Автоматическая SSO-привязка для этого сотрудника временно заблокирована "
            "администратором."
        ),
        "user_inactive": "Учётная запись CRM отключена. Обратитесь к администратору.",
    }

    def get(self, request):
        from django.urls import reverse
        from apps.crm.auth import OIDC_ERROR_SESSION_KEY

        if request.user.is_authenticated:
            return redirect("crm:dashboard")
        if request.GET.get("error") == "oidc":
            reason = request.session.pop(OIDC_ERROR_SESSION_KEY, "invalid_claims")
            return render(request, "crm/oidc_error.html", {
                "error_message": self.ERROR_MESSAGES.get(
                    reason,
                    "Не удалось завершить безопасный вход. Повторите попытку или обратитесь к администратору.",
                ),
                "retry_url": reverse("crm:login"),
            }, status=403)
        return redirect(f"{reverse('oidc_authentication_init')}?next=/crm/dashboard/")


class LogoutView(View):
    def post(self, request):
        from django.contrib.auth import logout

        logout(request)
        request.session.pop("active_workspace_id", None)
        return redirect("crm:login")


class WorkspaceSwitchView(CRMLoginMixin, View):
    def post(self, request):
        ws_id = request.POST.get("workspace_id")
        if ws_id:
            from apps.crm.models import WorkspaceMembership
            m = WorkspaceMembership.objects.filter(
                user=request.crm_user, workspace_id=ws_id, is_active=True
            ).first()
            if m:
                request.session["active_workspace_id"] = int(ws_id)
        return redirect(request.POST.get("next", "crm:dashboard"))


# ─── Dashboard ────────────────────────────────────────────────────────────────

class DashboardView(CRMLoginMixin, TemplateView):
    """
    Landing page for all authenticated users.

    Financial widgets require finance.view; report-only users go to moderation.
    """
    template_name = "crm/dashboard.html"
    required_permission = "dashboard.view"

    def get(self, request):
        if request.crm_access.allows("reports.view") and not request.crm_access.allows("finance.view"):
            return redirect("control:dashboard")

        ctx = self.get_crm_context(request)

        if request.crm_access.allows("finance.view"):
            from apps.crm.services import DashboardService
            status = DashboardService.get_today_status(request.crm_workspace)
            recent = DashboardService.get_recent_reports(request.crm_workspace, days=7)
            ctx.update({
                "status":  status,
                "recent":  recent,
                "now_msk": datetime.datetime.now(tz=_MSK),
            })

        return render(request, self.template_name, ctx)


# ─── Entry views (owner only) ─────────────────────────────────────────────────

class FinanceEntryView(CRMFinanceMixin, View):
    template_name = "crm/entry_finance.html"

    def get(self, request):
        date_str = request.GET.get("date", "")
        entry_date = self._parse_date(date_str)

        from apps.crm.services import EntryService
        from apps.crm.forms import FinanceEntryForm
        existing = EntryService.get_or_init_finance(request.crm_workspace, entry_date)
        form = FinanceEntryForm(instance=existing)

        ctx = self.get_crm_context(request)
        ctx.update({"form": form, "entry_date": entry_date, "existing": existing})
        return render(request, self.template_name, ctx)

    def post(self, request):
        date_str = request.POST.get("entry_date", "")
        entry_date = self._parse_date(date_str)

        from apps.crm.services import EntryService
        from apps.crm.forms import FinanceEntryForm
        existing = EntryService.get_or_init_finance(request.crm_workspace, entry_date)
        form = FinanceEntryForm(request.POST, request.FILES, instance=existing)

        if form.is_valid():
            data = form.cleaned_data
            screenshot = data.pop("kb_screenshot", None)
            if screenshot and screenshot != getattr(existing, "kb_screenshot", None):
                data["kb_screenshot"] = screenshot
            elif screenshot is False:
                data["kb_screenshot"] = None

            EntryService.save_finance_entry(
                request.crm_workspace, entry_date, request.crm_user, data
            )
            messages.success(request, f"Финансовые данные за {entry_date.strftime('%d.%m.%Y')} сохранены.")
            return redirect("crm:dashboard")

        ctx = self.get_crm_context(request)
        ctx.update({"form": form, "entry_date": entry_date, "existing": existing})
        return render(request, self.template_name, ctx)

    def _parse_date(self, date_str: str) -> datetime.date:
        try:
            return datetime.date.fromisoformat(date_str)
        except (ValueError, TypeError):
            return datetime.datetime.now(tz=_MSK).date()


class AdSlotCalculatorView(CRMPermissionMixin, TemplateView):
    """Stateless planner for the fixed seven daily advertising slots."""

    template_name = "crm/ad_calculator.html"
    required_permission = "calculator.use"
    defaults = {
        "weekly_target": "125000",
        "average_price": "5000",
        "paid_slots": "4",
        "vp_slots": "2",
        "repayment_slots": "1",
    }

    def get(self, request):
        from apps.crm.calculator import calculate_ad_slots
        from apps.crm.forms import AdSlotCalculatorForm

        form = AdSlotCalculatorForm(request.GET if request.GET else self.defaults)
        result = None
        slot_types = []
        if form.is_valid():
            values = form.cleaned_data
            result = calculate_ad_slots(
                weekly_target=values["weekly_target"],
                average_price=values["average_price"],
                paid_slots=values["paid_slots"],
            )
            slot_types = (
                ["paid"] * values["paid_slots"]
                + ["vp"] * values["vp_slots"]
                + ["repayment"] * values["repayment_slots"]
            )

        ctx = self.get_crm_context(request)
        ctx.update({"form": form, "result": result, "slot_types": slot_types})
        return render(request, self.template_name, ctx)


class ApplicationEntryView(CRMApplicationsMixin, View):
    template_name = "crm/entry_applications.html"

    def get(self, request):
        date_str = request.GET.get("date", "")
        entry_date = self._parse_date(date_str)

        from apps.crm.services import EntryService
        from apps.crm.forms import ApplicationEntryForm
        existing = EntryService.get_or_init_application(request.crm_workspace, entry_date)
        form = ApplicationEntryForm(instance=existing)

        ctx = self.get_crm_context(request)
        ctx.update({"form": form, "entry_date": entry_date, "existing": existing})
        return render(request, self.template_name, ctx)

    def post(self, request):
        date_str = request.POST.get("entry_date", "")
        entry_date = self._parse_date(date_str)

        from apps.crm.services import EntryService
        from apps.crm.forms import ApplicationEntryForm
        existing = EntryService.get_or_init_application(request.crm_workspace, entry_date)
        form = ApplicationEntryForm(request.POST, instance=existing)

        if form.is_valid():
            EntryService.save_application_entry(
                request.crm_workspace, entry_date, request.crm_user, form.cleaned_data
            )
            messages.success(request, f"Данные по заявкам за {entry_date.strftime('%d.%m.%Y')} сохранены.")
            return redirect("crm:dashboard")

        ctx = self.get_crm_context(request)
        ctx.update({"form": form, "entry_date": entry_date, "existing": existing})
        return render(request, self.template_name, ctx)

    def _parse_date(self, date_str: str) -> datetime.date:
        try:
            return datetime.date.fromisoformat(date_str)
        except (ValueError, TypeError):
            return datetime.datetime.now(tz=_MSK).date()


# ─── History & Reports (owner only) ──────────────────────────────────────────

class HistoryView(CRMOwnerMixin, TemplateView):
    required_permission = "analytics.view"
    template_name = "crm/history.html"

    def get(self, request):
        from apps.crm.forms import DateRangeForm
        from apps.crm.services import DashboardService

        today = datetime.datetime.now(tz=_MSK).date()
        form  = DateRangeForm(request.GET or None)

        if form.is_valid() and (form.cleaned_data.get("start") or form.cleaned_data.get("end")):
            start = form.cleaned_data.get("start") or (today - datetime.timedelta(days=30))
            end   = form.cleaned_data.get("end")   or today
        else:
            start = today - datetime.timedelta(days=29)
            end   = today

        entries = DashboardService.get_history_entries(request.crm_workspace, start, end)

        ctx = self.get_crm_context(request)
        ctx.update({
            "entries": entries,
            "form":    form,
            "start":   start,
            "end":     end,
            "today":   today,
        })
        return render(request, self.template_name, ctx)


class ReportDetailView(CRMOwnerMixin, TemplateView):
    required_permission = "analytics.view"
    template_name = "crm/report_detail.html"

    def get(self, request, pk):
        from apps.crm.models import DailySummaryReport
        report = get_object_or_404(
            DailySummaryReport, pk=pk, workspace=request.crm_workspace
        )
        ctx = self.get_crm_context(request)
        ctx["report"] = report
        return render(request, self.template_name, ctx)


class DayDetailView(CRMOwnerMixin, TemplateView):
    required_permission = "analytics.view"
    """Detail view for a single history day: finance entry, application entry, screenshot."""
    template_name = "crm/day_detail.html"

    def get(self, request, date_str: str):
        try:
            date = datetime.date.fromisoformat(date_str)
        except ValueError:
            raise Http404

        from apps.crm.models import FinanceEntry, ApplicationEntry, DailySummaryReport
        finance = FinanceEntry.objects.filter(workspace=request.crm_workspace, date=date).first()
        application = ApplicationEntry.objects.filter(workspace=request.crm_workspace, date=date).first()
        report = DailySummaryReport.objects.filter(workspace=request.crm_workspace, date=date).first()

        ctx = self.get_crm_context(request)
        ctx.update({
            "date": date,
            "finance": finance,
            "application": application,
            "report": report,
        })
        return render(request, self.template_name, ctx)


# ─── Admin views (owner only) ─────────────────────────────────────────────────

class AdminIndexView(CRMOwnerMixin, TemplateView):
    required_permission = "analytics.view"
    template_name = "crm/admin/index.html"

    def get(self, request):
        from apps.crm.models import DeadlineMiss, DailySummaryReport
        today   = datetime.datetime.now(tz=_MSK).date()
        month_start = today.replace(day=1)

        misses  = DeadlineMiss.objects.filter(
            workspace=request.crm_workspace, date__gte=month_start
        ).order_by("-date")
        reports = DailySummaryReport.objects.filter(
            workspace=request.crm_workspace, date__gte=month_start
        ).order_by("-date")[:10]

        ctx = self.get_crm_context(request)
        ctx.update({"misses": misses, "reports": reports, "today": today})
        return render(request, self.template_name, ctx)


class AdminMembersView(CRMOwnerMixin, View):
    """Redirects to the unified user management in control app."""
    def get(self, request):
        return redirect("control:users")

    def post(self, request):
        return redirect("control:users")



class GenerateReportView(CRMOwnerMixin, View):
    required_permission = "finance.manage"
    """Manual report generation for a given date."""

    def post(self, request, date_str: str):
        try:
            date = datetime.date.fromisoformat(date_str)
        except ValueError:
            raise Http404

        from apps.crm.models import FinanceEntry, ApplicationEntry
        from apps.crm.services import ReportService

        fin = FinanceEntry.objects.filter(workspace=request.crm_workspace, date=date).first()
        app = ApplicationEntry.objects.filter(workspace=request.crm_workspace, date=date).first()

        if not fin or not app:
            messages.error(request, f"Невозможно сформировать отчёт: не все данные внесены за {date.strftime('%d.%m.%Y')}.")
        else:
            ReportService.generate(
                request.crm_workspace, date, fin, app, generated_by=request.crm_user
            )
            messages.success(request, f"Отчёт за {date.strftime('%d.%m.%Y')} сформирован.")

        return redirect("crm:admin")


# ─── Export (owner only) ──────────────────────────────────────────────────────

class ExportView(CRMOwnerMixin, View):
    required_permission = "analytics.export"
    def get(self, request):
        from apps.crm.forms import DateRangeForm
        from apps.crm.services import ExportService

        today = datetime.datetime.now(tz=_MSK).date()
        form  = DateRangeForm(request.GET or None)
        if form.is_valid():
            start = form.cleaned_data.get("start") or today.replace(day=1)
            end   = form.cleaned_data.get("end")   or today
        else:
            start = today.replace(day=1)
            end   = today

        try:
            data = ExportService.export_to_excel(request.crm_workspace, start, end)
        except RuntimeError as exc:
            messages.error(request, str(exc))
            return redirect("crm:history")

        fname = f"{request.crm_workspace.slug}_{start}_{end}.xlsx"
        response = HttpResponse(
            data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{fname}"'
        return response
