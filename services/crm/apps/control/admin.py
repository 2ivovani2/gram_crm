from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from apps.control.models import (
    ReportTemplate, EmployeeReport, Penalty,
    KPISettings, KPIDocument, ControlSettings, ReportMedia,
)


@admin.register(ControlSettings)
class ControlSettingsAdmin(ModelAdmin):
    list_display = ["late_report_penalty_amount", "report_deadline_hour", "updated_at"]

    def has_add_permission(self, request):
        return not ControlSettings.objects.exists()


@admin.register(ReportTemplate)
class ReportTemplateAdmin(ModelAdmin):
    list_display = ["user", "updated_at", "updated_by"]
    search_fields = ["user__telegram_username", "user__telegram_id"]
    autocomplete_fields = ["user"]

    def save_model(self, request, obj, form, change):
        from apps.users.models import User
        obj.updated_by = User.objects.filter(
            telegram_id=request.user.telegram_id if hasattr(request.user, "telegram_id") else None
        ).first()
        super().save_model(request, obj, form, change)


@admin.register(EmployeeReport)
class EmployeeReportAdmin(ModelAdmin):
    list_display = ["id", "user", "status_badge", "period_label", "submitted_at", "reviewed_by"]
    list_filter = ["status", "submitted_at"]
    search_fields = ["user__telegram_username", "user__telegram_id"]
    readonly_fields = ["submitted_at", "updated_at", "reviewed_at", "telegram_file_id"]
    list_select_related = ["user", "reviewed_by"]

    def status_badge(self, obj):
        colors = {
            "not_submitted": "#6c757d",
            "pending":       "#fd7e14",
            "accepted":      "#28a745",
            "rejected":      "#dc3545",
            "revision":      "#007bff",
            "overdue":       "#721c24",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">{}</span>',
            color, obj.get_status_display(),
        )
    status_badge.short_description = "Статус"


@admin.register(ReportMedia)
class ReportMediaAdmin(ModelAdmin):
    list_display = ["id", "report", "revision", "position", "media_type", "status", "file_size", "created_at"]
    list_filter = ["status", "media_type", "created_at"]
    search_fields = ["report__id", "report__user__telegram_username", "original_filename"]
    readonly_fields = ["storage_key", "telegram_file_id", "created_at"]
    list_select_related = ["report", "report__user"]


@admin.register(Penalty)
class PenaltyAdmin(ModelAdmin):
    list_display = ["id", "user", "type", "amount", "status_badge", "reason", "created_at"]
    list_filter = ["type", "status", "created_at"]
    search_fields = ["user__telegram_username", "user__telegram_id", "reason"]
    readonly_fields = ["created_at", "updated_at", "resolved_at"]
    list_select_related = ["user", "created_by", "resolved_by"]

    def status_badge(self, obj):
        colors = {
            "created":   "#6c757d",
            "pending":   "#fd7e14",
            "accepted":  "#dc3545",
            "rejected":  "#28a745",
            "disputed":  "#007bff",
            "deleted":   "#adb5bd",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">{}</span>',
            color, obj.get_status_display(),
        )
    status_badge.short_description = "Статус"


@admin.register(KPISettings)
class KPISettingsAdmin(ModelAdmin):
    list_display = ["user", "base_rate", "bonus_rate", "penalty_rate", "updated_at"]
    search_fields = ["user__telegram_username", "user__telegram_id"]
    list_select_related = ["user"]


@admin.register(KPIDocument)
class KPIDocumentAdmin(ModelAdmin):
    list_display = ["user", "original_filename", "uploaded_at", "uploaded_by"]
    search_fields = ["user__telegram_username", "user__telegram_id"]
    list_select_related = ["user", "uploaded_by"]

    def save_model(self, request, obj, form, change):
        if obj.file and not obj.original_filename:
            obj.original_filename = obj.file.name.split("/")[-1]
        super().save_model(request, obj, form, change)
