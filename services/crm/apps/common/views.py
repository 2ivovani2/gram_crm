from django.http import JsonResponse
from django.shortcuts import render
from django.views import View
from django.db import connection


class LandingView(View):
    def get(self, request, *args, **kwargs):
        from django.conf import settings
        bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "").lstrip("@")
        welcome_bot_username = getattr(settings, "GRAMLY_HELLO_BOT_USERNAME", "").lstrip("@")
        return render(request, "landing.html", {
            "bot_username": bot_username,
            "welcome_bot_username": welcome_bot_username,
        })


class HealthCheckView(View):
    """Lightweight liveness probe for Docker / load balancer."""

    def get(self, request, *args, **kwargs):
        # Quick DB ping
        try:
            connection.ensure_connection()
            db_ok = True
        except Exception:
            db_ok = False

        status = 200 if db_ok else 503
        return JsonResponse({"status": "ok" if db_ok else "degraded", "db": db_ok}, status=status)


class VpnProbeView(View):
    """Tell the VPN gate whether this request reached the private CRM runtime."""

    def get(self, request, *args, **kwargs):
        from django.conf import settings

        response = JsonResponse({"private": not settings.PUBLIC_ACCESS_GATE})
        response["Access-Control-Allow-Origin"] = "*"
        response["Cache-Control"] = "no-store"
        return response

    def options(self, request, *args, **kwargs):
        response = JsonResponse({}, status=204)
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response["Access-Control-Max-Age"] = "600"
        response["Cache-Control"] = "no-store"
        return response
