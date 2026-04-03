from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import InviteKey, InviteActivation


class InviteActivationInline(TabularInline):
    model = InviteActivation
    extra = 0
    readonly_fields = ("user", "activated_at")
    can_delete = False


@admin.register(InviteKey)
class InviteKeyAdmin(ModelAdmin):
    list_display = ("key", "label", "is_active", "uses_count", "max_uses", "expires_at", "created_by", "created_at")
    list_filter = ("is_active",)
    search_fields = ("key", "label")
    readonly_fields = ("key", "uses_count", "created_at")
    inlines = [InviteActivationInline]


@admin.register(InviteActivation)
class InviteActivationAdmin(ModelAdmin):
    list_display = ("key", "user", "activated_at")
    readonly_fields = ("key", "user", "activated_at")
