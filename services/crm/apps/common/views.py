from django.http import JsonResponse
from django.shortcuts import render
from django.views import View
from django.db import connection


LEARNING_VIDEOS = (
    ("01-podklyuchenie-bota-i-kanala", "Подключение бота и канала", 24, "first_launch"),
    ("02-gotovyy-shablon", "Готовый шаблон", 26, "first_launch"),
    ("03-privetstvie-s-nulya", "Приветствие с нуля", 23, "first_launch"),
    ("04-oformlenie-soobshcheniya", "Оформление сообщения", 23, "first_launch"),
    ("05-tsepochka-i-zaderzhki", "Цепочка и задержки", 27, "first_launch"),
    ("06-proverka-i-zapusk", "Проверка и запуск", 25, "first_launch"),
    ("07-proshchalnoe-soobshchenie", "Прощальное сообщение", 28, "automation"),
    ("08-zayavki-na-vstuplenie", "Заявки на вступление", 23, "automation"),
    ("09-baza-podpischikov", "База подписчиков", 25, "automation"),
    ("10-rassylka", "Рассылка", 24, "automation"),
    ("11-rotatsiya-kanalov", "Ротация каналов", 23, "automation"),
    ("12-analitika", "Аналитика", 23, "automation"),
    ("13-tarif-business", "Тариф «Бизнес»", 24, "account"),
    ("14-partnerskaya-programma", "Партнёрская программа", 24, "account"),
)

LEARNING_CHAPTERS = (
    ("first_launch", "Первый запуск"),
    ("automation", "Автоматизация и рост"),
    ("account", "Аккаунт"),
)


class LandingView(View):
    def get(self, request, *args, **kwargs):
        from django.conf import settings
        bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "").lstrip("@")
        welcome_bot_username = getattr(settings, "GRAMLY_HELLO_BOT_USERNAME", "").lstrip("@")
        videos = [
            {
                "number": f"{index:02d}",
                "slug": slug,
                "title": title,
                "duration_seconds": duration,
                "chapter_key": chapter,
            }
            for index, (slug, title, duration, chapter) in enumerate(LEARNING_VIDEOS, 1)
        ]
        return render(request, "landing.html", {
            "bot_username": bot_username,
            "welcome_bot_username": welcome_bot_username,
            "learning_videos": videos,
            "learning_chapters": [
                {"key": key, "title": title, "videos": [v for v in videos if v["chapter_key"] == key]}
                for key, title in LEARNING_CHAPTERS
            ],
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
