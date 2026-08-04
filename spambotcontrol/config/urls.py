from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from apps.common.views import HealthCheckView, LandingView
from apps.telegram_bot.webhook import TelegramWebhookView

urlpatterns = [
    # Landing page (gramly.tech)
    path("", LandingView.as_view(), name="landing"),
    # Django admin (superuser/backup tool only)
    path("django-admin/", admin.site.urls),
    # Telegram webhook
    path("bot/webhook/", TelegramWebhookView.as_view(), name="telegram-webhook"),
    # Health check for Docker / load balancer
    path("health/", HealthCheckView.as_view(), name="health-check"),
    # CRM web service (crm.gramly.tech)
    path("crm/", include("apps.crm.urls", namespace="crm")),
    # Gramly Control HR dashboard
    path("crm/control/", include("apps.control.urls", namespace="control")),
    # Manager documentation
    path("docs/", include("apps.docs.urls", namespace="docs")),
]

# Serve media files in development (uploaded CRM screenshots etc.)
if settings.DEBUG:
    from django.conf.urls.static import static
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
