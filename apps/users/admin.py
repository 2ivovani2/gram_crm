from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import ChoicesDropdownFilter
from .models import User


@admin.register(User)
class UserAdmin(ModelAdmin, BaseUserAdmin):
    list_display = ("telegram_id", "display_name", "role", "status", "is_activated", "attracted_count", "balance", "created_at")
    list_filter_submit = True
    list_filter = (
        ("role", ChoicesDropdownFilter),
        ("status", ChoicesDropdownFilter),
        "is_activated",
        "is_blocked_bot",
    )
    search_fields = ("telegram_id", "telegram_username", "first_name", "last_name")
    readonly_fields = ("telegram_id", "balance", "created_at", "updated_at", "last_activity_at", "activated_at")
    ordering = ("-created_at",)

    fieldsets = (
        ("Telegram Identity", {"fields": ("telegram_id", "telegram_username", "first_name", "last_name")}),
        ("Role & Status", {"fields": ("role", "status", "is_activated", "activated_at", "is_blocked_bot")}),
        ("Referral", {"fields": ("referred_by",)}),
        ("Работа и метрики", {"fields": ("work_url", "attracted_count", "personal_rate", "referral_rate", "balance")}),
        ("Timestamps", {"fields": ("created_at", "updated_at", "last_activity_at")}),
        ("Auth (internal)", {"fields": ("username", "password"), "classes": ("collapse",)}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("telegram_id", "username", "role", "status"),
        }),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change and any(f in form.changed_data for f in ("attracted_count", "personal_rate", "referral_rate")):
            from apps.users.services import UserService
            UserService.recalculate_balance(obj)
            # If attracted_count changed, also recalculate referrer's balance
            if "attracted_count" in form.changed_data and obj.referred_by_id:
                referrer = User.objects.filter(pk=obj.referred_by_id).first()
                if referrer:
                    UserService.recalculate_balance(referrer)
