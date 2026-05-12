from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import ReferralLink


@admin.register(ReferralLink)
class ReferralLinkAdmin(ModelAdmin):
    list_display = ("user", "token", "created_at")
    search_fields = ("user__telegram_id", "user__telegram_username", "token")
    readonly_fields = ("token", "created_at")
