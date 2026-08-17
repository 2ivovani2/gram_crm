from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import UserDailyStats, GlobalRate

# DailyReport, MissedDay, RateConfig: removed from admin.
# Models remain in DB for historical data only; not part of active workflow.
# GlobalRate is managed via /stats/clients/ rate settings panel, not Django admin.
# InviteKey/InviteActivation: see apps/invites/admin.py (already removed).


@admin.register(UserDailyStats)
class UserDailyStatsAdmin(ModelAdmin):
    list_display = ("user", "date", "tasks_submitted", "tasks_completed", "tasks_rejected", "earned")
    list_filter = ("date",)
    search_fields = ("user__telegram_id", "user__telegram_username")
    readonly_fields = ("user", "date")


@admin.register(GlobalRate)
class GlobalRateAdmin(ModelAdmin):
    list_display = ("__str__", "updated_at", "updated_by")
    readonly_fields = ("updated_at", "updated_by")

    def has_add_permission(self, request):
        return not GlobalRate.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
