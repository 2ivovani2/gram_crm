from django.contrib import admin
from django.utils import timezone
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import ChoicesDropdownFilter
from .models import WithdrawalRequest, WithdrawalStatus


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(ModelAdmin):
    list_display = ("id", "user", "amount", "method", "details", "status", "processed_by", "created_at")
    list_filter_submit = True
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("method", ChoicesDropdownFilter),
    )
    search_fields = ("user__first_name", "user__telegram_username", "user__telegram_id", "details")
    readonly_fields = ("created_at", "updated_at", "processed_at", "processed_by", "admin_notifications", "status")
    ordering = ("-created_at",)
    actions = ["approve_selected", "reject_selected"]

    fieldsets = (
        ("Заявка", {"fields": ("user", "amount", "method", "details", "status")}),
        ("Обработка", {"fields": ("processed_by", "processed_at")}),
        ("Системное", {"fields": ("admin_notifications", "created_at", "updated_at")}),
    )

    @admin.action(description="✅ Одобрить выбранные заявки")
    def approve_selected(self, request, queryset):
        pending = queryset.filter(status=WithdrawalStatus.PENDING)
        count = 0
        for wr in pending:
            from apps.withdrawals.services import WithdrawalService
            admin_user = request.user if hasattr(request.user, "telegram_id") else None
            WithdrawalService.approve(wr, admin_user)
            count += 1
        self.message_user(request, f"Одобрено заявок: {count}")

    @admin.action(description="❌ Отклонить выбранные заявки")
    def reject_selected(self, request, queryset):
        pending = queryset.filter(status=WithdrawalStatus.PENDING)
        count = 0
        for wr in pending:
            from apps.withdrawals.services import WithdrawalService
            admin_user = request.user if hasattr(request.user, "telegram_id") else None
            WithdrawalService.reject(wr, admin_user)
            count += 1
        self.message_user(request, f"Отклонено заявок: {count}")
