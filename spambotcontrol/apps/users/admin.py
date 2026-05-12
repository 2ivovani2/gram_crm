from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import ChoicesDropdownFilter
from .models import User, WorkLink


class WorkLinkInline(TabularInline):
    model = WorkLink
    extra = 0
    fields = ("url", "attracted_count", "is_active", "created_at", "deactivated_at", "note")
    readonly_fields = ("created_at", "deactivated_at")
    ordering = ("-created_at",)
    show_change_link = False


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
    readonly_fields = (
        "telegram_id", "balance", "created_at", "updated_at", "last_activity_at", "activated_at",
        "earnings_breakdown_display",
    )
    ordering = ("-created_at",)
    inlines = [WorkLinkInline]

    fieldsets = (
        ("Telegram Identity", {"fields": ("telegram_id", "telegram_username", "first_name", "last_name")}),
        ("Role & Status", {"fields": ("role", "status", "is_activated", "activated_at", "is_blocked_bot")}),
        ("Referral", {"fields": ("referred_by",)}),
        ("Работа и метрики", {"fields": ("work_url", "attracted_count", "personal_rate", "referral_rate", "balance")}),
        ("Начисления (расчёт)", {"fields": ("earnings_breakdown_display",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at", "last_activity_at")}),
        ("Auth (internal)", {"fields": ("username", "password"), "classes": ("collapse",)}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("telegram_id", "username", "role", "status"),
        }),
    )

    @admin.display(description="Разбивка начислений")
    def earnings_breakdown_display(self, obj: User) -> str:
        from apps.users.services import UserService
        try:
            b = UserService.get_earnings_breakdown(obj)
        except Exception:
            return "—"
        return format_html(
            "<table style='border-collapse:collapse;font-size:13px'>"
            "<tr><td style='padding:2px 8px'>👤 Личное</td><td><b>{personal_earned} ₽</b></td></tr>"
            "<tr><td style='padding:2px 8px'>🤝 Реферальное</td><td><b>{referral_earned} ₽</b></td></tr>"
            "<tr><td style='padding:2px 8px;border-top:1px solid #ccc'>📊 Начислено</td><td style='border-top:1px solid #ccc'><b>{gross_earned} ₽</b></td></tr>"
            "<tr><td style='padding:2px 8px'>💸 Выведено</td><td><b>{withdrawn} ₽</b></td></tr>"
            "<tr><td style='padding:2px 8px'>✅ Баланс</td><td><b>{balance} ₽</b></td></tr>"
            "<tr><td style='padding:2px 8px;border-top:1px solid #ccc'>📈 Привлечено (всего)</td><td style='border-top:1px solid #ccc'><b>{total_attracted}</b></td></tr>"
            "<tr><td style='padding:2px 8px'>📈 Активная ссылка</td><td><b>{active_attracted}</b></td></tr>"
            "</table>",
            **{k: v for k, v in b.items()},
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change and any(f in form.changed_data for f in ("personal_rate", "referral_rate")):
            from apps.users.services import UserService
            UserService.recalculate_balance(obj)
            if "referral_rate" in form.changed_data and obj.referred_by_id:
                referrer = User.objects.filter(pk=obj.referred_by_id).first()
                if referrer:
                    UserService.recalculate_balance(referrer)
