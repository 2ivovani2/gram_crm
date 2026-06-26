"""
Web dashboard views for Gramly Control.
Accessible at /crm/control/ — uses the existing CRM session auth.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.http import HttpResponseForbidden
from django.utils import timezone
from django.db.models import Count, Sum, Q

from apps.crm.views import CRMLoginMixin
from apps.users.models import User, UserRole, UserStatus


def _is_control_admin(request) -> bool:
    return request.crm_user.is_admin() or request.crm_is_owner


class ControlAccessMixin(CRMLoginMixin):
    def check_crm_permissions(self, request):
        if not (_is_control_admin(request) or request.crm_user.is_accountant()):
            return HttpResponseForbidden("Доступ запрещён")

    def ctx(self, request, extra: dict) -> dict:
        """Merge CRM base context (sidebar data) with view-specific data."""
        base = self.get_crm_context(request)
        base.update(extra)
        return base


class AdminOnlyMixin(ControlAccessMixin):
    def check_crm_permissions(self, request):
        if not _is_control_admin(request):
            return HttpResponseForbidden("Только для администраторов")


# ── Dashboard overview ─────────────────────────────────────────────────────────

class ControlDashboardView(ControlAccessMixin, View):
    def get(self, request):
        from apps.control.models import EmployeeReport, Penalty, ReportStatus, PenaltyStatus
        from apps.withdrawals.models import WithdrawalRequest

        today = timezone.localdate()
        user = request.crm_user

        extra = {
            "page": "control_dashboard",
            "is_admin": _is_control_admin(request),
            "is_accountant": user.is_accountant(),
        }

        if _is_control_admin(request):
            extra.update({
                "pending_reports": EmployeeReport.objects.filter(status=ReportStatus.PENDING).count(),
                "pending_penalties": Penalty.objects.filter(
                    status__in=[PenaltyStatus.PENDING, PenaltyStatus.DISPUTED]
                ).count(),
                "pending_withdrawals": WithdrawalRequest.objects.filter(
                    status__in=["pending", "processing", "receipt_sent"]
                ).count(),
                "active_workers": User.objects.filter(role=UserRole.WORKER, status=UserStatus.ACTIVE).count(),
                "reports_submitted_today": EmployeeReport.objects.filter(submitted_at__date=today).count(),
                "recent_reports": EmployeeReport.objects.filter(
                    status=ReportStatus.PENDING
                ).select_related("user").order_by("-submitted_at")[:5],
                "recent_penalties": Penalty.objects.filter(
                    status__in=[PenaltyStatus.PENDING, PenaltyStatus.DISPUTED]
                ).select_related("user").order_by("-created_at")[:5],
            })

        if _is_control_admin(request) or user.is_accountant():
            extra["pending_withdrawals_list"] = WithdrawalRequest.objects.filter(
                status__in=["pending", "processing", "receipt_sent"]
            ).select_related("user").order_by("created_at")[:10]

        return render(request, "control/dashboard.html", self.ctx(request, extra))


# ── Reports ────────────────────────────────────────────────────────────────────

class ReportsListView(AdminOnlyMixin, View):
    def get(self, request):
        from apps.control.models import EmployeeReport, ReportStatus

        status_filter = request.GET.get("status", "")
        search = request.GET.get("q", "")

        qs = EmployeeReport.objects.select_related("user", "reviewed_by").order_by("-submitted_at")
        if status_filter:
            qs = qs.filter(status=status_filter)
        if search:
            qs = qs.filter(
                Q(user__telegram_username__icontains=search) |
                Q(user__telegram_id__icontains=search)
            )

        return render(request, "control/reports_list.html", self.ctx(request, {
            "page": "reports",
            "reports": qs[:100],
            "statuses": ReportStatus.choices,
            "status_filter": status_filter,
            "search": search,
        }))


class ReportDetailView(AdminOnlyMixin, View):
    def get(self, request, pk):
        from apps.control.models import EmployeeReport
        report = get_object_or_404(EmployeeReport.objects.select_related("user"), pk=pk)
        return render(request, "control/report_detail.html", self.ctx(request, {
            "page": "reports",
            "report": report,
        }))

    def post(self, request, pk):
        from apps.control.models import EmployeeReport
        from apps.control.services import ReportService

        report = get_object_or_404(EmployeeReport, pk=pk)
        action = request.POST.get("action")
        comment = request.POST.get("comment", "")
        admin = request.crm_user

        if action == "accept":
            ReportService.accept_report(report, admin, comment)
        elif action == "reject":
            ReportService.reject_report(report, admin, comment)
        elif action == "revision":
            ReportService.send_to_revision(report, admin, comment)

        return redirect("control:reports")


# ── Penalties ──────────────────────────────────────────────────────────────────

class PenaltiesListView(AdminOnlyMixin, View):
    def get(self, request):
        from apps.control.models import Penalty, PenaltyStatus, PenaltyType

        status_filter = request.GET.get("status", "")
        search = request.GET.get("q", "")

        qs = Penalty.objects.select_related("user", "created_by", "resolved_by").order_by("-created_at")
        if status_filter:
            qs = qs.filter(status=status_filter)
        if search:
            qs = qs.filter(
                Q(user__telegram_username__icontains=search) |
                Q(reason__icontains=search)
            )

        return render(request, "control/penalties_list.html", self.ctx(request, {
            "page": "penalties",
            "penalties": qs[:100],
            "statuses": PenaltyStatus.choices,
            "types": PenaltyType.choices,
            "status_filter": status_filter,
            "search": search,
        }))

    def post(self, request):
        from apps.control.services import PenaltyService
        from decimal import Decimal, InvalidOperation

        action = request.POST.get("action")
        admin = request.crm_user

        if action == "create":
            username = request.POST.get("username", "").lstrip("@").strip()
            try:
                amount = Decimal(request.POST.get("amount", "0"))
            except InvalidOperation:
                amount = Decimal("0")
            reason = request.POST.get("reason", "")
            comment = request.POST.get("comment", "")
            try:
                worker = User.objects.get(telegram_username__iexact=username)
                PenaltyService.create_manual(admin, worker, amount, reason, comment)
            except User.DoesNotExist:
                pass

        return redirect("control:penalties")


class PenaltyActionView(AdminOnlyMixin, View):
    def post(self, request, pk):
        from apps.control.models import Penalty
        penalty = get_object_or_404(Penalty, pk=pk)
        action = request.POST.get("action")
        admin = request.crm_user

        if action == "accept":
            penalty.accept(admin)
        elif action == "reject":
            penalty.reject(admin)
        elif action == "delete":
            penalty.delete_soft(admin)

        return redirect("control:penalties")


# ── KPI / Employees ────────────────────────────────────────────────────────────

class EmployeesView(AdminOnlyMixin, View):
    def get(self, request):
        workers = User.objects.filter(
            role=UserRole.WORKER
        ).prefetch_related("kpi_settings", "kpi_document", "report_template").order_by("telegram_username")
        return render(request, "control/employees.html", self.ctx(request, {
            "page": "employees",
            "workers": workers,
        }))


class EmployeeKPIView(AdminOnlyMixin, View):
    def get(self, request, user_id):
        from apps.control.models import KPISettings, KPIDocument, ReportTemplate
        worker = get_object_or_404(User, pk=user_id, role=UserRole.WORKER)
        kpi, _ = KPISettings.objects.get_or_create(user=worker)
        doc = KPIDocument.objects.filter(user=worker).first()
        template = ReportTemplate.objects.filter(user=worker).first()

        return render(request, "control/employee_kpi.html", self.ctx(request, {
            "page": "employees",
            "worker": worker,
            "kpi": kpi,
            "doc": doc,
            "template": template,
        }))

    def post(self, request, user_id):
        from apps.control.models import KPISettings, KPIDocument, ReportTemplate
        from decimal import Decimal, InvalidOperation

        worker = get_object_or_404(User, pk=user_id, role=UserRole.WORKER)
        admin = request.crm_user
        action = request.POST.get("action")

        if action == "save_kpi":
            def _d(key):
                try:
                    return Decimal(request.POST.get(key, "0"))
                except InvalidOperation:
                    return Decimal("0")

            kpi, _ = KPISettings.objects.get_or_create(user=worker)
            kpi.base_rate = _d("base_rate")
            kpi.bonus_rate = _d("bonus_rate")
            kpi.penalty_rate = _d("penalty_rate")
            kpi.other_info = request.POST.get("other_info", "")
            kpi.updated_by = admin
            kpi.save()

        elif action == "save_template":
            content = request.POST.get("content", "")
            ReportTemplate.objects.update_or_create(
                user=worker,
                defaults={"content": content, "updated_by": admin},
            )

        elif action == "upload_doc" and request.FILES.get("doc_file"):
            f = request.FILES["doc_file"]
            doc, _ = KPIDocument.objects.get_or_create(user=worker)
            doc.file = f
            doc.original_filename = f.name
            doc.uploaded_by = admin
            doc.save()

        return redirect("control:employee_kpi", user_id=user_id)


# ── Withdrawals ────────────────────────────────────────────────────────────────

class WithdrawalsListView(ControlAccessMixin, View):
    def get(self, request):
        from apps.withdrawals.models import WithdrawalRequest, WithdrawalStatus

        status_filter = request.GET.get("status", "")
        qs = WithdrawalRequest.objects.select_related("user", "processed_by").order_by("-created_at")
        if status_filter:
            qs = qs.filter(status=status_filter)

        return render(request, "control/withdrawals_list.html", self.ctx(request, {
            "page": "withdrawals",
            "withdrawals": qs[:100],
            "statuses": WithdrawalStatus.choices,
            "status_filter": status_filter,
        }))


class WithdrawalActionView(ControlAccessMixin, View):
    def post(self, request, pk):
        from apps.withdrawals.models import WithdrawalRequest
        from apps.control.services import ControlWithdrawalService

        w = get_object_or_404(WithdrawalRequest, pk=pk)
        action = request.POST.get("action")
        user = request.crm_user

        if action == "processing":
            ControlWithdrawalService.mark_processing(w, user)
        elif action == "receipt_sent":
            ControlWithdrawalService.mark_receipt_sent(w, user)
        elif action == "completed":
            ControlWithdrawalService.mark_completed(w, user)
        elif action == "reject":
            ControlWithdrawalService.reject(w, user)

        return redirect("control:withdrawals")


# ── Analytics ─────────────────────────────────────────────────────────────────

class AnalyticsView(ControlAccessMixin, View):
    def get(self, request):
        from apps.control.models import EmployeeReport, Penalty, PenaltyStatus
        from apps.withdrawals.models import WithdrawalRequest, WithdrawalStatus
        from datetime import timedelta
        from django.db.models.functions import TruncDate

        today = timezone.localdate()

        reports_by_status = dict(
            EmployeeReport.objects.filter(submitted_at__date__gte=today - timedelta(days=30))
            .values("status").annotate(c=Count("id")).values_list("status", "c")
        )

        penalties_total = Penalty.objects.filter(
            status=PenaltyStatus.ACCEPTED
        ).aggregate(total=Sum("amount"))["total"] or 0

        withdrawals_total = WithdrawalRequest.objects.filter(
            status=WithdrawalStatus.APPROVED
        ).aggregate(total=Sum("amount"))["total"] or 0

        workers = User.objects.filter(role=UserRole.WORKER).prefetch_related("penalties", "reports")

        daily_reports = list(
            EmployeeReport.objects
            .filter(submitted_at__date__gte=today - timedelta(days=14))
            .annotate(day=TruncDate("submitted_at"))
            .values("day").annotate(count=Count("id")).order_by("day")
        )

        return render(request, "control/analytics.html", self.ctx(request, {
            "page": "analytics",
            "reports_by_status": reports_by_status,
            "penalties_total": penalties_total,
            "withdrawals_total": withdrawals_total,
            "workers": workers,
            "daily_reports": daily_reports,
        }))


# ── User management ───────────────────────────────────────────────────────────

class UsersListView(AdminOnlyMixin, View):
    def get(self, request):
        role_filter = request.GET.get("role", "")
        status_filter = request.GET.get("status", "")
        search = request.GET.get("q", "")

        qs = User.objects.order_by("telegram_username")
        if role_filter:
            qs = qs.filter(role=role_filter)
        if status_filter:
            qs = qs.filter(status=status_filter)
        if search:
            qs = qs.filter(
                Q(telegram_username__icontains=search) |
                Q(telegram_id__icontains=search)
            )

        return render(request, "control/users_list.html", self.ctx(request, {
            "page": "users",
            "users": qs[:200],
            "roles": UserRole.choices,
            "statuses": UserStatus.choices,
            "role_filter": role_filter,
            "status_filter": status_filter,
            "search": search,
        }))

    def post(self, request):
        """Create a new user slot (pre-register before they start the bot)."""
        telegram_id = request.POST.get("telegram_id", "").strip()
        username = request.POST.get("telegram_username", "").lstrip("@").strip()
        role = request.POST.get("role", UserRole.WORKER)

        if telegram_id:
            User.objects.get_or_create(
                telegram_id=telegram_id,
                defaults={
                    "telegram_username": username or None,
                    "role": role,
                    "status": UserStatus.ACTIVE,
                    "is_activated": True,
                },
            )
        return redirect("control:users")


class UserEditView(AdminOnlyMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        action = request.POST.get("action")

        if action == "set_role":
            new_role = request.POST.get("role")
            if new_role in dict(UserRole.choices):
                user.role = new_role
                user.save(update_fields=["role"])

        elif action == "set_status":
            new_status = request.POST.get("status")
            if new_status in dict(UserStatus.choices):
                user.status = new_status
                user.save(update_fields=["status"])

        elif action == "delete":
            # Only allow deleting non-admin users
            if not user.is_admin():
                user.delete()
                return redirect("control:users")

        return redirect(request.POST.get("next", "control:users"))
