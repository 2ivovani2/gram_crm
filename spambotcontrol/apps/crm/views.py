"""
CRM web views.

URL prefix: /crm/

Auth: Authentik OIDC → Django session. Only an existing active CRM user whose
      Telegram username matches the initial OIDC username can authenticate.

Access model (two levels):
  Authenticated  — any User in DB; sees a limited dashboard only.
  Owner          — WorkspaceMembership.role == OWNER for the active workspace;
                   full access to all pages and actions.

Mixins:
  CRMLoginMixin  — authentication only (any DB user)
  CRMOwnerMixin  — owner-only; returns 403 for authenticated non-owners

The permission check runs BEFORE the view method via check_crm_permissions()
hook — CRMLoginMixin.dispatch() calls it after setting up request attributes
but before delegating to super().dispatch() (which routes to get/post).
"""
from __future__ import annotations

import datetime
import logging
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.http import HttpResponse, Http404
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
      crm_is_owner    — bool; True for OWNER membership OR UserRole.ADMIN bot role

    User.role is the single source of truth for access level:
      ADMIN      → crm_is_owner = True (full access, no WorkspaceMembership required)
      ACCOUNTANT → accountant access (Control module)
      WORKER     → no CRM access
    WorkspaceMembership is still used when present, but bot ADMIN always gets owner-level access.
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
        request.crm_user = request.user

        # 3. Resolve workspace + membership
        workspace, membership = self._resolve_workspace_and_membership(request)
        request.crm_workspace = workspace
        request.crm_membership = membership

        # 4. Bot ADMIN role always grants owner-level CRM access (single source of truth)
        request.crm_is_owner = (
            bool(membership and membership.is_owner())
            or request.crm_user.is_admin()
        )

        # 5. Permission hook — subclasses return a response to deny, None to allow
        denial = self.check_crm_permissions(request)
        if denial is not None:
            return denial

        # 6. Run the actual view method
        return super().dispatch(request, *args, **kwargs)

    def _resolve_workspace_and_membership(self, request):
        """
        Return (workspace, membership) for the current user.
        Bot admins without membership still get the default workspace resolved.
        """
        from apps.crm.models import WorkspaceMembership, Workspace

        ws_id = request.session.get("active_workspace_id")
        if ws_id:
            m = WorkspaceMembership.objects.filter(
                user=request.crm_user, workspace_id=ws_id, is_active=True
            ).select_related("workspace").first()
            if m:
                return m.workspace, m

        # Auto-select first active membership
        m = WorkspaceMembership.objects.filter(
            user=request.crm_user, is_active=True
        ).select_related("workspace").order_by("workspace__name").first()
        if m:
            request.session["active_workspace_id"] = m.workspace_id
            return m.workspace, m

        # Bot admin with no explicit membership → resolve default workspace
        if request.crm_user.is_admin():
            ws = Workspace.objects.order_by("created_at").first()
            if ws:
                request.session["active_workspace_id"] = ws.pk
                return ws, None

        return None, None

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

        # Derive capabilities: membership takes precedence; bot admin gets everything
        can_finance = bool(m and m.can_enter_finance()) or user.is_admin()
        can_apps    = bool(m and m.can_enter_applications()) or user.is_admin()

        # Role label: prefer bot role for clarity
        role_label = user.get_role_display() if hasattr(user, "get_role_display") else (
            m.get_role_display() if m else None
        )

        return {
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


class CRMOwnerMixin(CRMLoginMixin):
    """Restrict to OWNER membership or bot ADMIN role."""

    def check_crm_permissions(self, request):
        if not request.crm_is_owner:
            return render(request, "crm/403.html", self.get_crm_context(request), status=403)
        return None


class CRMFinanceMixin(CRMLoginMixin):
    """Allow OWNER/ADMIN + FINANCE roles."""

    def check_crm_permissions(self, request):
        m = request.crm_membership
        if not (request.crm_is_owner or (m and m.can_enter_finance())):
            return render(request, "crm/403.html", self.get_crm_context(request), status=403)
        return None


class CRMApplicationsMixin(CRMLoginMixin):
    """Allow OWNER/ADMIN + APPLICATIONS roles."""

    def check_crm_permissions(self, request):
        m = request.crm_membership
        if not (request.crm_is_owner or (m and m.can_enter_applications())):
            return render(request, "crm/403.html", self.get_crm_context(request), status=403)
        return None


# ─── Auth views ───────────────────────────────────────────────────────────────

class LoginView(TemplateView):
    template_name = "crm/login.html"

    def get(self, request):
        from django.urls import reverse

        if request.user.is_authenticated:
            return redirect("crm:dashboard")
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

    Owners see the full operational dashboard (today's status, recent reports,
    weekly plan progress).
    Non-owners see a limited welcome screen with a clear "contact owner" message.
    No financial data is exposed to non-owners.
    """
    template_name = "crm/dashboard.html"

    def get(self, request):
        ctx = self.get_crm_context(request)

        if request.crm_is_owner:
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
