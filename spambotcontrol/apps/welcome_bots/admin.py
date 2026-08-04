from django.contrib import admin

from .models import Channel, Contact, EventLog, GreetingDelivery, JoinRequest, ManagedBot, Owner, WelcomeMessageVersion


@admin.register(Owner)
class OwnerAdmin(admin.ModelAdmin):
    list_display = ("telegram_id", "username", "guide_completed", "created_at")
    search_fields = ("telegram_id", "username")


@admin.register(ManagedBot)
class ManagedBotAdmin(admin.ModelAdmin):
    list_display = ("username", "telegram_id", "owner", "is_active", "webhook_configured", "created_at")
    list_filter = ("is_active", "webhook_configured", "auto_approve")
    search_fields = ("username", "telegram_id", "owner__telegram_id")
    readonly_fields = ("token_ciphertext", "webhook_secret", "path_secret", "public_id")


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ("title", "telegram_id", "bot", "is_active", "can_invite_users")
    list_filter = ("is_active", "can_invite_users")


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("telegram_id", "username", "bot", "delivery_status", "language_code", "bot_started")
    list_filter = ("delivery_status", "gender", "language_code", "bot_started")
    search_fields = ("telegram_id", "username")


admin.site.register(JoinRequest)
admin.site.register(GreetingDelivery)
admin.site.register(WelcomeMessageVersion)
admin.site.register(EventLog)
