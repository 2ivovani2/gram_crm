"""
CRM web views.

URL prefix: /crm/

Auth: Telegram Login Widget → HMAC-verified → Django session.
      Any user that exists in the database can authenticate.

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
      crm_workspace   — Workspace or None (if user has no membership)
      crm_membership  — WorkspaceMembership or None
      crm_is_owner    — bool; True only when membership.role == OWNER
    """

    def dispatch(self, request, *args, **kwargs):
        # 1. Session check
        user_id = request.session.get("crm_user_id")
        if not user_id:
            return redirect("crm:login")

        # 2. Load user
        from apps.users.models import User
        try:
            request.crm_user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            del request.session["crm_user_id"]
            return redirect("crm:login")

        # 3. Resolve workspace + membership (None for non-members)
        workspace, membership = self._resolve_workspace_and_membership(request)
        request.crm_workspace = workspace
        request.crm_membership = membership
        request.crm_is_owner = bool(membership and membership.is_owner())

        # 4. Permission hook — subclasses return a response to deny, None to allow
        denial = self.check_crm_permissions(request)
        if denial is not None:
            return denial

        # 5. Run the actual view method
        return super().dispatch(request, *args, **kwargs)

    def _resolve_workspace_and_membership(self, request):
        """
        Return (workspace, membership) for the current user.
        Non-members (no WorkspaceMembership) get (None, None).
        """
        from apps.crm.models import WorkspaceMembership

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
        # Always load all memberships so sidebar switcher works from every page
        all_memberships = list(
            WorkspaceMembership.objects.filter(user=request.crm_user, is_active=True)
            .select_related("workspace")
            .order_by("workspace__name")
        )
        m = request.crm_membership
        return {
            "crm_user":         request.crm_user,
            "workspace":        request.crm_workspace,
            "membership":       m,
            "all_workspaces":   [ms.workspace for ms in all_memberships],
            "all_memberships":  all_memberships,   # includes role per workspace
            "is_owner":         request.crm_is_owner,
            "can_enter_finance":      bool(m and m.can_enter_finance()),
            "can_enter_applications": bool(m and m.can_enter_applications()),
            "crm_role":         m.get_role_display() if m else None,
        }


class CRMOwnerMixin(CRMLoginMixin):
    """Restrict to OWNER role."""

    def check_crm_permissions(self, request):
        if not request.crm_is_owner:
            return render(request, "crm/403.html", self.get_crm_context(request), status=403)
        return None


class CRMFinanceMixin(CRMLoginMixin):
    """Allow OWNER and FINANCE roles."""

    def check_crm_permissions(self, request):
        m = request.crm_membership
        if not m or not m.can_enter_finance():
            return render(request, "crm/403.html", self.get_crm_context(request), status=403)
        return None


class CRMApplicationsMixin(CRMLoginMixin):
    """Allow OWNER and APPLICATIONS roles."""

    def check_crm_permissions(self, request):
        m = request.crm_membership
        if not m or not m.can_enter_applications():
            return render(request, "crm/403.html", self.get_crm_context(request), status=403)
        return None


# ─── Auth views ───────────────────────────────────────────────────────────────

class LoginView(TemplateView):
    template_name = "crm/login.html"

    def get(self, request):
        if request.session.get("crm_user_id"):
            return redirect("crm:dashboard")
        from django.conf import settings
        from django.urls import reverse
        bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "") or ""
        error = None
        if not bot_username:
            error = (
                "Конфигурация CRM не завершена: "
                "переменная TELEGRAM_BOT_USERNAME не задана в .env. "
                "Добавьте имя бота (без @) и перезапустите сервис."
            )
            logger.error("TELEGRAM_BOT_USERNAME is not set — CRM login widget will not work")
        # Must be absolute HTTPS URL — Telegram's OAuth server validates the domain
        # and redirects the popup to this URL. A relative path causes oauth.telegram.org
        # to redirect to its own domain → 404 → silent failure → user back on login.
        auth_callback_url = request.build_absolute_uri(reverse("crm:auth_callback"))
        return render(request, self.template_name, {
            "bot_username": bot_username,
            "auth_callback_url": auth_callback_url,
            "error": error,
        })


class TelegramAuthCallbackView(View):
    """
    Telegram Login Widget callback.

    Access rule: any user that exists in users.User (telegram_id match) can
    authenticate. No workspace membership required for login itself.
    Non-members land on the dashboard with a limited "no access" view.

    If the user is not in the DB at all, login is denied with a clear message:
    they must start the bot first to be registered.
    """

    def get(self, request):
        from django.conf import settings
        from apps.crm.services import verify_telegram_login, TelegramAuthError

        params = dict(request.GET)
        flat = {k: v[0] if isinstance(v, list) else v for k, v in params.items()}

        try:
            data = verify_telegram_login(flat, settings.TELEGRAM_BOT_TOKEN)
        except TelegramAuthError as exc:
            logger.warning("CRM auth failed: %s", exc)
            return render(request, "crm/login.html", {
                "error": "Ошибка авторизации через Telegram. Попробуйте ещё раз.",
                "bot_username": getattr(settings, "TELEGRAM_BOT_USERNAME", ""),
            })

        telegram_id = int(data["id"])

        from apps.users.models import User
        user = User.objects.filter(telegram_id=telegram_id).first()

        if user is None:
            # User doesn't exist in DB — must start the bot first
            return render(request, "crm/login.html", {
                "error": (
                    "Вы не зарегистрированы в системе. "
                    "Сначала запустите бота — он зарегистрирует вас автоматически."
                ),
                "bot_username": getattr(settings, "TELEGRAM_BOT_USERNAME", ""),
            })

        request.session["crm_user_id"] = user.pk
        request.session["crm_user_name"] = user.display_name
        logger.info("CRM login: user %s (tg_id=%s)", user.display_name, telegram_id)
        return redirect("crm:dashboard")


class LogoutView(View):
    def post(self, request):
        request.session.pop("crm_user_id", None)
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
            from apps.crm.services import DashboardService, WeeklyPlanService
            status = DashboardService.get_today_status(request.crm_workspace)
            recent = DashboardService.get_recent_reports(request.crm_workspace, days=7)
            plan   = WeeklyPlanService.get_current_plan(request.crm_workspace)
            ctx.update({
                "status":  status,
                "recent":  recent,
                "plan":    plan,
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
    template_name = "crm/admin/members.html"

    def get(self, request):
        from apps.crm.models import WorkspaceMembership
        from apps.crm.forms import AddMemberForm
        members = WorkspaceMembership.objects.filter(
            workspace=request.crm_workspace
        ).select_related("user").order_by("role", "user__first_name")
        add_form = AddMemberForm()
        ctx = self.get_crm_context(request)
        ctx.update({"members": members, "add_form": add_form})
        return render(request, self.template_name, ctx)

    def post(self, request):
        action = request.POST.get("action", "")

        if action == "add":
            from apps.crm.forms import AddMemberForm
            from apps.crm.services import WorkspaceService
            from apps.users.models import User
            form = AddMemberForm(request.POST)
            if form.is_valid():
                tg_id = form.cleaned_data["telegram_id"]
                role  = form.cleaned_data["role"]
                user  = User.objects.filter(telegram_id=tg_id).first()
                if not user:
                    messages.error(request, f"Пользователь с Telegram ID {tg_id} не найден.")
                else:
                    WorkspaceService.add_member(
                        request.crm_workspace, user, role, invited_by=request.crm_user
                    )
                    messages.success(request, f"{user.display_name} добавлен с ролью {role}.")

        elif action == "change_role":
            from apps.crm.services import WorkspaceService
            from apps.users.models import User
            user_id = request.POST.get("user_id")
            role    = request.POST.get("role")
            if user_id and role:
                user = get_object_or_404(User, pk=user_id)
                WorkspaceService.set_member_role(request.crm_workspace, user, role)
                messages.success(request, f"Роль пользователя {user.display_name} обновлена.")

        elif action == "deactivate":
            from apps.crm.models import WorkspaceMembership
            member_id = request.POST.get("member_id")
            if member_id:
                WorkspaceMembership.objects.filter(
                    pk=member_id, workspace=request.crm_workspace
                ).update(is_active=False)
                messages.success(request, "Доступ участника отозван.")

        return redirect("crm:admin_members")


class AdminPlansView(CRMOwnerMixin, View):
    template_name = "crm/admin/plans.html"

    def get(self, request):
        from apps.crm.models import WeeklyPlan
        from apps.crm.forms import WeeklyPlanForm
        from apps.crm.services import WeeklyPlanService

        plans = WeeklyPlan.objects.filter(
            workspace=request.crm_workspace
        ).order_by("-week_start")[:12]

        today = datetime.datetime.now(tz=_MSK).date()
        week_start = WeeklyPlanService.get_week_start(today)
        form = WeeklyPlanForm(initial={"week_start": week_start})

        ctx = self.get_crm_context(request)
        ctx.update({"plans": plans, "form": form})
        return render(request, self.template_name, ctx)

    def post(self, request):
        from apps.crm.forms import WeeklyPlanForm
        from apps.crm.services import WeeklyPlanService
        form = WeeklyPlanForm(request.POST)
        if form.is_valid():
            WeeklyPlanService.upsert_plan(
                workspace=request.crm_workspace,
                week_start=form.cleaned_data["week_start"],
                pp_plan=form.cleaned_data["pp_plan"],
                privat_plan=form.cleaned_data["privat_plan"],
                created_by=request.crm_user,
            )
            messages.success(request, "План сохранён.")
            return redirect("crm:admin_plans")

        from apps.crm.models import WeeklyPlan
        plans = WeeklyPlan.objects.filter(workspace=request.crm_workspace).order_by("-week_start")[:12]
        ctx = self.get_crm_context(request)
        ctx.update({"plans": plans, "form": form})
        return render(request, self.template_name, ctx)


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
