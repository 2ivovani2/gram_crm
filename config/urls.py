from django.contrib import admin
from django.urls import path
from apps.common.views import HealthCheckView, LandingView
from apps.stats.views import StatsDashboardView
from apps.telegram_bot.webhook import TelegramWebhookView

urlpatterns = [
    # Landing page
    path("", LandingView.as_view(), name="landing"),
    # Stats dashboard (staff_member_required — superuser login via /django-admin/)
    path("stats/", StatsDashboardView.as_view(), name="stats-dashboard"),
    # Django admin (superuser/backup tool only — main admin is inside Telegram bot)
    path("django-admin/", admin.site.urls),
    # Telegram webhook
    path("bot/webhook/", TelegramWebhookView.as_view(), name="telegram-webhook"),
    # Health check for Docker / load balancer
    path("health/", HealthCheckView.as_view(), name="health-check"),
]
