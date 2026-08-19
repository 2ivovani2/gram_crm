from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
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
    # Shared Authentik OIDC callback/init/logout endpoints.
    path("oidc/", include("mozilla_django_oidc.urls")),
    # Gramly Control HR dashboard
    path("crm/control/", include("apps.control.urls", namespace="control")),
    path("crm/owners/", include("apps.owners.urls", namespace="owners")),
    # The legacy bundled docs were replaced by the private Outline service.
    path("docs/", RedirectView.as_view(url="https://docs.gramly.tech/", permanent=False)),
]

# Serve media files in development (uploaded CRM screenshots etc.)
if settings.DEBUG:
    from django.conf.urls.static import static
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
