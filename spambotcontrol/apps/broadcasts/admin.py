from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import Broadcast, BroadcastDeliveryLog


class DeliveryLogInline(TabularInline):
    model = BroadcastDeliveryLog
    extra = 0
    readonly_fields = ("user", "status", "error_message", "sent_at")
    can_delete = False
    show_change_link = False
    max_num = 50


@admin.register(Broadcast)
class BroadcastAdmin(ModelAdmin):
    list_display = ("title", "audience", "status", "total_recipients", "sent_count", "failed_count", "created_at")
    list_filter = ("status", "audience")
    search_fields = ("title",)
    readonly_fields = ("status", "celery_task_id", "total_recipients", "sent_count", "failed_count",
                       "started_at", "finished_at", "created_at", "updated_at")
    inlines = [DeliveryLogInline]


@admin.register(BroadcastDeliveryLog)
class BroadcastDeliveryLogAdmin(ModelAdmin):
    list_display = ("broadcast", "user", "status", "sent_at")
    list_filter = ("status",)
    readonly_fields = ("broadcast", "user", "status", "error_message", "sent_at")
