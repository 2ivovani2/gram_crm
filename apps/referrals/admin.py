from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import ReferralLink, ReferralSettings


@admin.register(ReferralLink)
class ReferralLinkAdmin(ModelAdmin):
    list_display = ("user", "token", "created_at")
    search_fields = ("user__telegram_id", "user__telegram_username", "token")
    readonly_fields = ("token", "created_at")


@admin.register(ReferralSettings)
class ReferralSettingsAdmin(ModelAdmin):
    list_display = ("rate_percent", "updated_by", "updated_at")
    readonly_fields = ("updated_by", "updated_at")

    def has_add_permission(self, request):
        return not ReferralSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
