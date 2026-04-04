from django.http import JsonResponse
from django.shortcuts import render
from django.views import View
from django.db import connection


class LandingView(View):
    def get(self, request, *args, **kwargs):
        from django.conf import settings
        bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "")
        return render(request, "landing.html", {"bot_username": bot_username})


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
