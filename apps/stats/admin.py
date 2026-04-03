from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import UserDailyStats, SystemStats


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
