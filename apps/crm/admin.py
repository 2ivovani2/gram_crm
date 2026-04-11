from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from apps.crm.models import (
    Workspace, WorkspaceMembership, WeeklyPlan,
    FinanceEntry, ApplicationEntry, DailySummaryReport, DeadlineMiss,
)


class MembershipInline(TabularInline):
    model       = WorkspaceMembership
    extra       = 1
    fields      = ["user", "role", "is_active"]
    autocomplete_fields = ["user"]


@admin.register(Workspace)
class WorkspaceAdmin(ModelAdmin):
    list_display  = ["name", "slug", "is_active", "created_by", "created_at"]
    list_filter   = ["is_active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    inlines       = [MembershipInline]


@admin.register(WorkspaceMembership)
class WorkspaceMembershipAdmin(ModelAdmin):
    list_display  = ["user", "workspace", "role", "is_active", "joined_at"]
    list_filter   = ["workspace", "role", "is_active"]
    search_fields = ["user__first_name", "user__telegram_username"]
    autocomplete_fields = ["user"]


@admin.register(WeeklyPlan)
class WeeklyPlanAdmin(ModelAdmin):
    list_display  = ["workspace", "week_start", "pp_plan", "privat_plan"]
    list_filter   = ["workspace"]
    ordering      = ["-week_start"]


@admin.register(FinanceEntry)
class FinanceEntryAdmin(ModelAdmin):
    list_display  = ["workspace", "date", "income", "expenses", "pp_earnings", "privat_earnings", "submitted_by"]
    list_filter   = ["workspace", "date"]
    search_fields = ["workspace__name"]
    date_hierarchy = "date"
    ordering      = ["-date"]


@admin.register(ApplicationEntry)
class ApplicationEntryAdmin(ModelAdmin):
    list_display  = ["workspace", "date", "applications_count", "applications_earnings", "submitted_by"]
    list_filter   = ["workspace", "date"]
    date_hierarchy = "date"
    ordering      = ["-date"]


@admin.register(DailySummaryReport)
class DailySummaryReportAdmin(ModelAdmin):
    list_display  = ["workspace", "date", "pp_earnings", "privat_earnings",
                     "applications_count", "cash_flow_balance", "telegram_sent", "generated_at"]
    list_filter   = ["workspace", "telegram_sent"]
    date_hierarchy = "date"
    readonly_fields = ["report_text", "generated_at"]
    ordering      = ["-date"]


@admin.register(DeadlineMiss)
class DeadlineMissAdmin(ModelAdmin):
    list_display  = ["workspace", "date", "finance_missing", "applications_missing", "notified_at"]
    list_filter   = ["workspace", "finance_missing", "applications_missing"]
    date_hierarchy = "date"
    ordering      = ["-date"]
