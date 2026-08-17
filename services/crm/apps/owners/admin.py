from django.contrib import admin

from .models import OwnerChannel, OwnerStatus, TelegramOwner


@admin.register(TelegramOwner)
class TelegramOwnerAdmin(admin.ModelAdmin):
    list_display = ("label", "workspace", "rank", "health", "status", "archived_at")
    list_filter = ("workspace", "rank", "status", "archived_at")
    search_fields = ("phone", "telegram_username", "telegram_id", "display_name")
    exclude = ("session_ciphertext", "twofa_ciphertext", "proxy_ciphertext", "sim_ciphertext")


admin.site.register(OwnerStatus)
admin.site.register(OwnerChannel)
