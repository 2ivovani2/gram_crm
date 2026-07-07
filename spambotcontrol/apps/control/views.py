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


class AdminOrCuratorMixin(ControlAccessMixin):
    """Allows access for admins, owners AND curators."""
    def check_crm_permissions(self, request):
        if not (_is_control_admin(request) or request.crm_user.is_curator()):
            return HttpResponseForbidden("Только для администраторов и кураторов")


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
            from apps.control.models import REPORT_MODERATION_STATUSES
            extra.update({
                "pending_reports": EmployeeReport.objects.filter(
                    status__in=REPORT_MODERATION_STATUSES
                ).count(),
                "pending_penalties": Penalty.objects.filter(
                    status__in=[PenaltyStatus.PENDING, PenaltyStatus.DISPUTED]
                ).count(),
                "pending_withdrawals": WithdrawalRequest.objects.filter(
                    status__in=["pending", "processing", "receipt_sent"]
                ).count(),
                "active_workers": User.objects.filter(role=UserRole.WORKER, status=UserStatus.ACTIVE).count(),
                "reports_submitted_today": EmployeeReport.objects.filter(submitted_at__date=today).count(),
                "recent_reports": EmployeeReport.objects.filter(
                    status__in=REPORT_MODERATION_STATUSES
                ).select_related("user", "template").order_by("-submitted_at")[:5],
                "recent_penalties": Penalty.objects.filter(
                    status__in=[PenaltyStatus.PENDING, PenaltyStatus.DISPUTED]
                ).select_related("user", "created_by").order_by("-created_at")[:5],
            })

        if _is_control_admin(request) or user.is_accountant():
            extra["pending_withdrawals_list"] = WithdrawalRequest.objects.filter(
                status__in=["pending", "processing", "receipt_sent"]
            ).select_related("user").order_by("created_at")[:10]

        return render(request, "control/dashboard.html", self.ctx(request, extra))


# ── Report templates ───────────────────────────────────────────────────────────

class ReportTemplatesView(AdminOrCuratorMixin, View):
    def get(self, request):
        from apps.control.models import ReportTemplate
        templates = (
            ReportTemplate.objects
            .select_related("created_by")
            .prefetch_related("assigned_users")
            .order_by("name", "-updated_at")
        )
        return render(request, "control/report_templates.html", self.ctx(request, {
            "page": "reports",
            "templates": templates,
        }))

    def post(self, request):
        from apps.control.models import ReportTemplate
        from apps.users.models import User as U

        action = request.POST.get("action")
        admin = request.crm_user

        if action == "create":
            name = request.POST.get("name", "").strip()
            if not name:
                return redirect("control:report_templates")
            tmpl = ReportTemplate.objects.create(
                name=name,
                description=request.POST.get("description", ""),
                format_instructions=request.POST.get("format_instructions", ""),
                correction_deadline_hours=int(request.POST.get("correction_deadline_hours", 24) or 24),
                created_by=admin,
                updated_by=admin,
            )
            try:
                from decimal import Decimal
                tmpl.auto_penalty_amount = Decimal(request.POST.get("auto_penalty_amount", "0") or "0")
                tmpl.save(update_fields=["auto_penalty_amount"])
            except Exception:
                pass
            return redirect("control:report_template_edit", pk=tmpl.pk)

        return redirect("control:report_templates")


class ReportTemplateEditView(AdminOrCuratorMixin, View):
    def get(self, request, pk):
        from apps.control.models import ReportTemplate
        from apps.users.models import User as U, UserRole

        tmpl = get_object_or_404(ReportTemplate, pk=pk)
        workers = U.objects.filter(role=UserRole.WORKER).order_by("telegram_username")
        assigned_ids = set(tmpl.assigned_users.values_list("id", flat=True))
        times = tmpl.notification_times or []

        return render(request, "control/report_template_edit.html", self.ctx(request, {
            "page": "reports",
            "tmpl": tmpl,
            "workers": workers,
            "assigned_ids": assigned_ids,
            "notif_time_1": times[0] if len(times) > 0 else "",
            "notif_time_2": times[1] if len(times) > 1 else "",
            "notif_time_3": times[2] if len(times) > 2 else "",
        }))

    def post(self, request, pk):
        from apps.control.models import ReportTemplate
        from apps.users.models import User as U, UserRole
        from decimal import Decimal, InvalidOperation

        tmpl = get_object_or_404(ReportTemplate, pk=pk)
        action = request.POST.get("action", "save")
        admin = request.crm_user

        if action == "delete":
            tmpl.delete()
            return redirect("control:report_templates")

        # Save template fields
        tmpl.name = request.POST.get("name", tmpl.name).strip()
        tmpl.description = request.POST.get("description", "")
        tmpl.format_instructions = request.POST.get("format_instructions", "")
        try:
            tmpl.correction_deadline_hours = int(request.POST.get("correction_deadline_hours", 24) or 24)
        except (ValueError, TypeError):
            tmpl.correction_deadline_hours = 24
        try:
            tmpl.auto_penalty_amount = Decimal(request.POST.get("auto_penalty_amount", "0") or "0")
        except InvalidOperation:
            pass
        # notification_times: collect up to 3 non-empty HH:MM values
        notif_times = []
        for i in range(1, 4):
            t = request.POST.get(f"notification_time_{i}", "").strip()
            if t:
                notif_times.append(t[:5])
        tmpl.notification_times = notif_times

        tmpl.updated_by = admin
        tmpl.save()

        # Update assigned users (M2M)
        selected_ids = request.POST.getlist("assigned_users")
        try:
            selected_pks = [int(i) for i in selected_ids if i.isdigit()]
        except Exception:
            selected_pks = []
        tmpl.assigned_users.set(U.objects.filter(pk__in=selected_pks, role=UserRole.WORKER))

        return redirect("control:report_template_edit", pk=tmpl.pk)


# ── Reports ────────────────────────────────────────────────────────────────────

class ReportsListView(AdminOrCuratorMixin, View):
    def get(self, request):
        from apps.control.models import EmployeeReport, ReportStatus, ReportTemplate
        from apps.users.models import User as U, UserRole

        status_filter = request.GET.get("status", "")
        search = request.GET.get("q", "")
        template_filter = request.GET.get("template", "")
        admin_filter = request.GET.get("admin", "")
        date_from = request.GET.get("date_from", "")
        date_to = request.GET.get("date_to", "")
        overdue_only = request.GET.get("overdue", "")

        qs = (
            EmployeeReport.objects
            .select_related("user", "reviewed_by", "template", "template__created_by")
            .order_by("-submitted_at")
        )
        if status_filter:
            qs = qs.filter(status=status_filter)
        if search:
            qs = qs.filter(
                Q(user__telegram_username__icontains=search) |
                Q(user__telegram_id__icontains=search)
            )
        if template_filter:
            qs = qs.filter(template_id=template_filter)
        if admin_filter:
            qs = qs.filter(template__created_by_id=admin_filter)
        if date_from:
            qs = qs.filter(submitted_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(submitted_at__date__lte=date_to)
        if overdue_only:
            qs = qs.filter(status=ReportStatus.OVERDUE)

        templates = ReportTemplate.objects.filter(name__gt="").order_by("name")
        admins = U.objects.filter(role=UserRole.ADMIN).order_by("telegram_username")

        return render(request, "control/reports_list.html", self.ctx(request, {
            "page": "reports",
            "reports": qs[:200],
            "statuses": ReportStatus.choices,
            "templates": templates,
            "admins": admins,
            "status_filter": status_filter,
            "search": search,
            "template_filter": template_filter,
            "admin_filter": admin_filter,
            "date_from": date_from,
            "date_to": date_to,
            "overdue_only": overdue_only,
        }))


class ReportDetailView(AdminOrCuratorMixin, View):
    def get(self, request, pk):
        from apps.control.models import EmployeeReport
        report = get_object_or_404(
            EmployeeReport.objects.select_related("user", "template", "reviewed_by"), pk=pk
        )
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
        else:
            qs = qs.exclude(status=PenaltyStatus.DELETED)
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
        ).prefetch_related("kpi_settings", "kpi_document", "report_template", "assigned_report_templates").order_by("telegram_username")
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
        from apps.crm.models import WorkspaceMembership, CRMRole

        role_filter = request.GET.get("role", "anonymous")
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

        # Pop flash messages (set by invite POST)
        request.session.pop("invite_error", None)
        request.session.pop("invite_success", None)

        users = list(qs[:200])

        # Attach CRM membership for the active workspace to each user
        workspace = request.crm_workspace
        if workspace:
            memberships = {
                m.user_id: m
                for m in WorkspaceMembership.objects.filter(
                    workspace=workspace,
                    user_id__in=[u.pk for u in users],
                    is_active=True,
                )
            }
        else:
            memberships = {}
        for u in users:
            u.crm_membership = memberships.get(u.pk)

        role_counts = dict(
            User.objects.values("role").annotate(c=Count("id")).values_list("role", "c")
        )

        return render(request, "control/users_list.html", self.ctx(request, {
            "page": "users",
            "users": users,
            "roles": UserRole.choices,
            "statuses": UserStatus.choices,
            "crm_roles": CRMRole.choices,
            "role_filter": role_filter,
            "status_filter": status_filter,
            "search": search,
            "anon_count":       role_counts.get("anonymous", 0),
            "worker_count":     role_counts.get("worker", 0),
            "curator_count":    role_counts.get("curator", 0),
            "accountant_count": role_counts.get("accountant", 0),
            "admin_count":      role_counts.get("admin", 0),
        }))

    def post(self, request):
        """Search user by telegram_id or @username and send bot invite."""
        from apps.control.models import WorkerInvite, InviteStatus
        from apps.control.bot.invite_handlers import send_worker_invite_sync

        query = request.POST.get("query", "").strip().lstrip("@")
        error = None
        success = None

        if not query:
            return redirect("control:users")

        # Find user: try numeric ID first, then username
        user = None
        if query.isdigit():
            user = User.objects.filter(telegram_id=int(query)).first()
        if not user:
            user = User.objects.filter(telegram_username__iexact=query).first()

        if not user:
            error = f"Пользователь «{query}» не найден. Попросите их написать /start боту."
        elif user.is_admin():
            error = "Нельзя отправить приглашение администратору."
        elif user.role == UserRole.WORKER:
            error = f"@{user.telegram_username or user.telegram_id} уже является сотрудником."
        else:
            # Check no pending invite already
            existing = WorkerInvite.objects.filter(
                user=user, status=InviteStatus.PENDING
            ).first()
            if existing:
                error = f"Приглашение уже отправлено @{user.telegram_username or user.telegram_id} и ожидает ответа."
            else:
                invite = WorkerInvite.objects.create(
                    user=user,
                    invited_by=request.crm_user,
                )
                inviter = request.crm_user
                inviter_name = (
                    f"@{inviter.telegram_username}" if inviter.telegram_username
                    else inviter.first_name or str(inviter.telegram_id)
                )
                send_worker_invite_sync(invite.pk, user.telegram_id, inviter_name)
                success = f"✅ Приглашение отправлено @{user.telegram_username or user.telegram_id}"

        # Pass flash message via session
        if error:
            request.session["invite_error"] = error
        if success:
            request.session["invite_success"] = success
        return redirect("control:users")


class UserEditView(AdminOnlyMixin, View):
    def post(self, request, pk):
        from apps.crm.models import WorkspaceMembership, CRMRole
        from apps.crm.services import WorkspaceService

        user = get_object_or_404(User, pk=pk)
        action = request.POST.get("action")
        next_url = request.POST.get("next", "control:users")

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

        elif action == "set_crm_role":
            crm_role = request.POST.get("crm_role", "")
            workspace = request.crm_workspace
            if workspace and crm_role in dict(CRMRole.choices):
                WorkspaceService.add_member(workspace, user, crm_role, invited_by=request.crm_user)
                # CRM owner → sync bot role to admin (single source of truth)
                if crm_role == "owner" and not user.is_admin():
                    user.role = UserRole.ADMIN
                    user.save(update_fields=["role"])

        elif action == "remove_crm_role":
            workspace = request.crm_workspace
            if workspace:
                WorkspaceMembership.objects.filter(
                    workspace=workspace, user=user
                ).update(is_active=False)

        elif action == "delete":
            if not user.is_admin():
                user.delete()
                return redirect("control:users")

        return redirect(next_url)


# ── Deadline notifications ─────────────────────────────────────────────────────

class DeadlineNotificationsView(AdminOnlyMixin, View):
    def get(self, request):
        from apps.control.models import DeadlineNotificationLog, NotificationSlot, NotificationStatus
        from apps.users.models import User as U, UserRole

        date_filter   = request.GET.get("date", "")
        slot_filter   = request.GET.get("slot", "")
        status_filter = request.GET.get("status", "")
        user_filter   = request.GET.get("user", "")

        qs = DeadlineNotificationLog.objects.select_related("user").order_by("-attempted_at")

        if date_filter:
            qs = qs.filter(deadline_date=date_filter)
        if slot_filter:
            qs = qs.filter(slot=slot_filter)
        if status_filter:
            qs = qs.filter(status=status_filter)
        if user_filter:
            qs = qs.filter(user__telegram_username__icontains=user_filter)

        logs = qs[:200]

        # Workers eligible but not yet in log for today (never processed)
        import datetime as dt
        import pytz
        _MSK = pytz.timezone("Europe/Moscow")
        today = dt.datetime.now(tz=_MSK).date()

        workers_without_log_today = U.objects.filter(
            role=UserRole.WORKER
        ).exclude(
            deadline_notifications__deadline_date=today
        ).only("pk", "telegram_id", "telegram_username")

        return render(request, "control/deadline_notifications.html", self.ctx(request, {
            "page": "deadline_notifications",
            "logs": logs,
            "slots": NotificationSlot.choices,
            "statuses": NotificationStatus.choices,
            "date_filter": date_filter,
            "slot_filter": slot_filter,
            "status_filter": status_filter,
            "user_filter": user_filter,
            "today": today,
            "workers_without_log": workers_without_log_today,
        }))

    def post(self, request):
        """Send a test notification to a specific user."""
        from apps.control.tasks import _send_message_sync
        from apps.users.models import User as U
        from django.http import JsonResponse

        user_id = request.POST.get("user_id")
        if not user_id:
            return JsonResponse({"ok": False, "error": "user_id required"})

        try:
            worker = U.objects.get(pk=int(user_id))
        except (U.DoesNotExist, ValueError):
            return JsonResponse({"ok": False, "error": "Пользователь не найден"})

        text = (
            f"🔔 <b>[Тест] GRAMLY CRM — проверка уведомлений</b>\n\n"
            f"Этот тест был отправлен вручную из CRM администратором.\n"
            f"Если вы видите это сообщение — бот работает корректно ✅"
        )
        ok = _send_message_sync(worker.telegram_id, text)
        username = worker.telegram_username or str(worker.telegram_id)
        if ok:
            return JsonResponse({"ok": True, "msg": f"✅ Сообщение отправлено @{username}"})
        else:
            return JsonResponse({"ok": False, "error": f"Ошибка отправки для @{username} (tg_id={worker.telegram_id})"})
