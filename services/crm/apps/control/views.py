"""
Web dashboard views for Gramly Control.
Accessible at /crm/control/ — uses the existing CRM session auth.
"""
import logging
import re

from django.contrib import messages
from django.core.files.storage import default_storage
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.http import FileResponse, HttpResponse, HttpResponseForbidden, StreamingHttpResponse
from django.utils.http import content_disposition_header
from django.utils import timezone
from django.db.models import Count, Sum, Q, F

from apps.crm.views import CRMLoginMixin
from apps.users.models import User, UserRole, UserStatus

logger = logging.getLogger(__name__)


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
        from apps.control.models import EmployeeReport, Penalty, PenaltyStatus
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
                "reports_submitted_today": EmployeeReport.objects.filter(report_date=today).count(),
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
                # The report lifecycle has a single product-wide correction
                # window. Keep the legacy field populated for compatibility,
                # but never accept a per-template override from the browser.
                correction_deadline_hours=1,
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
        workers = U.objects.exclude(role=UserRole.ADMIN).order_by("role", "telegram_username")
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
        # Fixed by the report lifecycle specification. The model field remains
        # for backwards compatibility with existing data and integrations.
        tmpl.correction_deadline_hours = 1
        try:
            tmpl.auto_penalty_amount = Decimal(request.POST.get("auto_penalty_amount", "0") or "0")
        except InvalidOperation:
            pass
        # deadline_time — required HH:MM field
        import datetime as _dt
        deadline_time_raw = request.POST.get("deadline_time", "").strip()
        if deadline_time_raw:
            try:
                parts = deadline_time_raw.split(":")
                tmpl.deadline_time = _dt.time(int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
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
        tmpl.assigned_users.set(U.objects.filter(pk__in=selected_pks).exclude(role=UserRole.ADMIN))

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
            qs = qs.filter(report_date__gte=date_from)
        if date_to:
            qs = qs.filter(report_date__lte=date_to)
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
        from apps.control.models import EmployeeReport, ModerationHistory
        from apps.control.services import ReportService

        report = get_object_or_404(
            EmployeeReport.objects.select_related("user", "template", "reviewed_by"), pk=pk
        )
        history = list(
            ModerationHistory.objects.filter(report=report)
            .select_related("moderator")
            .order_by("created_at")
        )
        current_media = list(
            report.media_files.filter(revision=report.current_revision)
            .order_by("position", "id")
        )
        can_moderate = ReportService.can_moderate(request.crm_user, report)
        return render(request, "control/report_detail.html", self.ctx(request, {
            "page": "reports",
            "report": report,
            "history": history,
            "current_media": current_media,
            "can_moderate": can_moderate,
        }))

    def post(self, request, pk):
        from apps.control.models import EmployeeReport
        from apps.control.services import ReportService

        report = get_object_or_404(
            EmployeeReport.objects.select_related("user", "template"), pk=pk
        )
        action = request.POST.get("action")
        comment = request.POST.get("comment", "")
        admin = request.crm_user

        if not ReportService.can_moderate(admin, report):
            return redirect("control:report_detail", pk=pk)

        if action == "accept":
            ReportService.accept_report(report, admin, comment)
        elif action == "reject":
            ReportService.reject_report(report, admin, comment)
        elif action == "revision":
            ReportService.send_to_revision(report, admin, comment)

        return redirect("control:reports")


class ReportMediaView(AdminOrCuratorMixin, View):
    """Authorized media stream; storage object URLs are never exposed."""

    _range_re = re.compile(r"^bytes=(\d*)-(\d*)$")

    @staticmethod
    def _chunks(handle, remaining, chunk_size=256 * 1024):
        try:
            while remaining > 0:
                chunk = handle.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            handle.close()

    def get(self, request, report_pk, media_pk):
        from apps.control.models import ReportMedia, ReportMediaStatus

        media = get_object_or_404(
            ReportMedia.objects.select_related("report"),
            pk=media_pk,
            report_id=report_pk,
        )
        if media.status != ReportMediaStatus.READY or not media.storage_key:
            return HttpResponse("Файл недоступен", status=404, content_type="text/plain; charset=utf-8")

        try:
            size = media.file_size or default_storage.size(media.storage_key)
            handle = default_storage.open(media.storage_key, "rb")
        except Exception:
            logger.exception("Unable to open report media id=%s", media.pk)
            return HttpResponse("Файл недоступен", status=404, content_type="text/plain; charset=utf-8")

        requested_download = request.GET.get("download") == "1"
        filename = media.original_filename or f"report-{report_pk}-file-{media.pk}"
        content_type = media.mime_type or "application/octet-stream"
        safe_inline = (
            content_type in {
                "image/jpeg", "image/png", "image/gif", "image/webp",
                "application/pdf", "text/plain",
            }
            or content_type.startswith("video/")
        )
        download = requested_download or not safe_inline
        if not safe_inline:
            content_type = "application/octet-stream"
        range_header = request.headers.get("Range", "")
        match = self._range_re.match(range_header)
        if not match:
            response = FileResponse(
                handle,
                as_attachment=download,
                filename=filename,
                content_type=content_type,
            )
            response["Accept-Ranges"] = "bytes"
            response["Cache-Control"] = "private, no-store"
            response["X-Content-Type-Options"] = "nosniff"
            return response

        first, last = match.groups()
        if not first and not last:
            handle.close()
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        else:
            suffix = min(int(last), size)
            start, end = size - suffix, size - 1
        if start >= size or start > end:
            handle.close()
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response

        handle.seek(start)
        length = end - start + 1
        response = StreamingHttpResponse(
            self._chunks(handle, length),
            status=206,
            content_type=content_type,
        )
        response["Content-Length"] = str(length)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Accept-Ranges"] = "bytes"
        response["Content-Disposition"] = content_disposition_header(download, filename)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


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
                penalty = PenaltyService.create_manual(admin, worker, amount, reason, comment)
                from apps.control.tasks import queue_penalty_notification
                queue_penalty_notification(penalty.pk)
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
        from apps.control.services import ControlBalanceService

        workers = list(User.objects.exclude(
            role=UserRole.ANONYMOUS
        ).prefetch_related("kpi_settings", "kpi_document").order_by("role", "telegram_username"))
        for worker in workers:
            worker.balance_snapshot = ControlBalanceService.get_balance_snapshot(worker)

        return render(request, "control/employees.html", self.ctx(request, {
            "page": "employees",
            "workers": workers,
        }))


class EmployeeKPIView(AdminOnlyMixin, View):
    def get(self, request, user_id):
        from apps.control.models import KPISettings, KPIDocument
        from apps.control.services import ControlBalanceService
        worker = get_object_or_404(User, pk=user_id)
        kpi, _ = KPISettings.objects.get_or_create(user=worker)
        doc = KPIDocument.objects.filter(user=worker).first()
        balance = ControlBalanceService.get_balance_snapshot(worker)

        return render(request, "control/employee_kpi.html", self.ctx(request, {
            "page": "employees",
            "worker": worker,
            "kpi": kpi,
            "doc": doc,
            "saved": request.GET.get("saved"),
            "total_balance": balance["gross"],
            "available_balance": balance["available"],
            "withdrawn": balance["withdrawn"],
            "penalties": balance["penalties"],
        }))

    def post(self, request, user_id):
        from apps.control.models import KPIDocument
        from apps.control.services import ControlBalanceService
        from decimal import Decimal, InvalidOperation

        worker = get_object_or_404(User, pk=user_id)
        admin = request.crm_user
        action = request.POST.get("action")

        def _d(key, default="0"):
            try:
                v = request.POST.get(key, default)
                return Decimal(v) if v else Decimal(default)
            except InvalidOperation:
                return Decimal(default)

        if action == "save_kpi":
            worker.daily_rate = _d("daily_rate")
            worker.save(update_fields=["daily_rate", "updated_at"])

        elif action == "edit_balance":
            ControlBalanceService.set_available_balance(
                worker,
                _d("available_balance"),
            )

        elif action == "upload_doc" and request.FILES.get("doc_file"):
            f = request.FILES["doc_file"]
            doc, _ = KPIDocument.objects.get_or_create(user=worker)
            doc.file = f
            doc.original_filename = f.name
            doc.uploaded_by = admin
            doc.save()

        from django.http import HttpResponseRedirect
        return HttpResponseRedirect(f"{request.path}?saved=1")


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

        today = timezone.localdate()

        reports_by_status = dict(
            EmployeeReport.objects.filter(report_date__gte=today - timedelta(days=30))
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
            .filter(report_date__gte=today - timedelta(days=14))
            .values(day=F("report_date")).annotate(count=Count("id")).order_by("day")
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

        elif action == "unlink_oidc":
            if user.pk == request.crm_user.pk:
                messages.error(request, "Нельзя разорвать собственную активную SSO-связь.")
            elif user.oidc_subject:
                from apps.crm.identity import terminate_user_sessions

                user.oidc_subject = None
                user.oidc_binding_blocked = True
                user.save(update_fields=[
                    "oidc_subject",
                    "oidc_binding_blocked",
                    "updated_at",
                ])
                revoked = terminate_user_sessions(user.pk)
                logger.info(
                    "crm_oidc_unlinked crm_user_id=%s actor_user_id=%s sessions_revoked=%s",
                    user.pk,
                    request.crm_user.pk,
                    revoked,
                )
                messages.success(
                    request,
                    "SSO-связь разорвана. Новая привязка заблокирована до ручного разрешения.",
                )

        elif action == "allow_oidc_rebind":
            if user.oidc_subject:
                messages.error(request, "Сначала разорвите существующую SSO-связь.")
            else:
                user.oidc_binding_blocked = False
                user.save(update_fields=["oidc_binding_blocked", "updated_at"])
                logger.info(
                    "crm_oidc_rebind_allowed crm_user_id=%s actor_user_id=%s",
                    user.pk,
                    request.crm_user.pk,
                )
                messages.success(
                    request,
                    "Новая SSO-привязка разрешена. Проверьте Telegram ID в Authentik.",
                )

        elif action == "delete":
            if not user.is_admin():
                # Employee records own financial history, reports, penalties and
                # audit trails through cascading relations. "Delete" therefore
                # archives access instead of physically deleting that history.
                from apps.control.services import EmployeeService

                EmployeeService.archive(user)
                messages.success(
                    request,
                    "Сотрудник архивирован. Отчёты, штрафы и финансовая история сохранены.",
                )
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

        import datetime as dt
        from zoneinfo import ZoneInfo
        _MSK = ZoneInfo("Europe/Moscow")
        now_msk = dt.datetime.now(tz=_MSK)
        today = now_msk.date()

        # Only meaningful after 23:00 — before that the reminder task hasn't run yet
        notifications_started = now_msk.hour >= 23

        if notifications_started:
            workers_without_log_today = U.objects.filter(
                role=UserRole.WORKER
            ).exclude(
                deadline_notifications__deadline_date=today
            ).only("pk", "telegram_id", "telegram_username")
        else:
            workers_without_log_today = U.objects.none()

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
            "notifications_started": notifications_started,
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
            "🔔 <b>[Тест] GRAMLY CRM — проверка уведомлений</b>\n\n"
            "Этот тест был отправлен вручную из CRM администратором.\n"
            "Если вы видите это сообщение — бот работает корректно ✅"
        )
        ok = _send_message_sync(worker.telegram_id, text)
        username = worker.telegram_username or str(worker.telegram_id)
        if ok:
            return JsonResponse({"ok": True, "msg": f"✅ Сообщение отправлено @{username}"})
        else:
            return JsonResponse({"ok": False, "error": f"Ошибка отправки для @{username} (tg_id={worker.telegram_id})"})


# ── Global settings ────────────────────────────────────────────────────────────

class ControlSettingsView(AdminOnlyMixin, View):
    def get(self, request):
        from apps.control.models import ControlSettings
        from apps.users.models import User as _User, UserStatus, UserRole
        settings = ControlSettings.get()
        processor_options = _User.objects.filter(
            status=UserStatus.ACTIVE,
            role__in=[UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CURATOR],
        ).order_by("telegram_username")
        selected_processor_ids = set(settings.withdrawal_processors.values_list("pk", flat=True))
        return render(request, "control/settings.html", self.ctx(request, {
            "page": "settings",
            "settings": settings,
            "processor_options": processor_options,
            "selected_processor_ids": selected_processor_ids,
        }))

    def post(self, request):
        from apps.control.models import ControlSettings
        from decimal import Decimal, InvalidOperation
        settings = ControlSettings.get()

        try:
            settings.late_report_penalty_amount = Decimal(
                request.POST.get("late_report_penalty_amount", "0") or "0"
            )
        except InvalidOperation:
            pass

        try:
            h = int(request.POST.get("report_deadline_hour", 23) or 23)
            settings.report_deadline_hour = max(0, min(23, h))
        except (ValueError, TypeError):
            pass

        try:
            drh = int(request.POST.get("daily_rate_hour", 20) or 20)
            settings.daily_rate_hour = max(0, min(23, drh))
        except (ValueError, TypeError):
            pass

        try:
            settings.min_withdrawal_amount = Decimal(
                request.POST.get("min_withdrawal_amount", "1000") or "1000"
            )
        except InvalidOperation:
            pass

        settings.updated_by = request.crm_user
        settings.save()

        # Update M2M withdrawal_processors
        from apps.users.models import User as _User
        processor_ids = request.POST.getlist("withdrawal_processors")
        try:
            processor_pks = [int(x) for x in processor_ids if x.isdigit()]
        except (ValueError, AttributeError):
            processor_pks = []
        settings.withdrawal_processors.set(_User.objects.filter(pk__in=processor_pks))

        return redirect("control:settings")
