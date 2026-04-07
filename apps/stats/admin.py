from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import UserDailyStats, SystemStats, RateConfig, DailyReport


@admin.register(UserDailyStats)
class UserDailyStatsAdmin(ModelAdmin):
    list_display = ("user", "date", "tasks_submitted", "tasks_completed", "tasks_rejected", "earned")
    list_filter = ("date",)
    search_fields = ("user__telegram_id", "user__telegram_username")
    readonly_fields = ("user", "date")


@admin.register(SystemStats)
class SystemStatsAdmin(ModelAdmin):
    list_display = ("date", "total_users", "active_users", "new_users", "total_tasks", "total_broadcasts")
    readonly_fields = ("date",)


@admin.register(RateConfig)
class RateConfigAdmin(ModelAdmin):
    list_display = ("__str__", "worker_share", "referral_share", "updated_at", "updated_by")
    readonly_fields = ("updated_at", "updated_by")

    def has_add_permission(self, request):
        # Allow adding only if no instance exists (singleton)
        return not RateConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DailyReport)
class DailyReportAdmin(ModelAdmin):
    list_display = (
        "date", "client_nick", "client_rate", "total_applications",
        "worker_rate", "referral_rate", "our_profit", "broadcast_sent", "created_by",
    )
    list_filter = ("broadcast_sent", "date")
    search_fields = ("client_nick",)
    readonly_fields = (
        "worker_rate", "referral_rate", "our_profit",
        "broadcast_sent", "created_by", "created_at", "updated_at",
    )
    fieldsets = (
        ("Данные клиента", {
            "fields": ("date", "link", "client_nick", "client_rate", "total_applications"),
        }),
        ("Вычисленные ставки", {
            "fields": ("worker_rate", "referral_rate", "our_profit"),
        }),
        ("Метаданные", {
            "fields": ("broadcast_sent", "created_by", "created_at", "updated_at"),
        }),
    )
