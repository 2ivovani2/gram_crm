"""
Manager documentation views.

Access: any User that exists in the database (same Telegram auth as CRM).
Session key: crm_user_id (shared with CRM — if logged into CRM, docs works too).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

logger = logging.getLogger(__name__)


# ─── Auth mixin ───────────────────────────────────────────────────────────────

class DocsLoginMixin:
    """Require any authenticated DB user (same session as CRM)."""

    def dispatch(self, request, *args, **kwargs):
        if not request.session.get("crm_user_id"):
            next_url = request.get_full_path()
            return redirect(f"{reverse('docs:login')}?next={next_url}")

        from apps.users.models import User
        try:
            request.docs_user = User.objects.get(pk=request.session["crm_user_id"])
        except User.DoesNotExist:
            del request.session["crm_user_id"]
            return redirect("docs:login")

        return super().dispatch(request, *args, **kwargs)

    def get_docs_context(self, request):
        return {"docs_user": request.docs_user}


# ─── Auth views ───────────────────────────────────────────────────────────────

class DocsLoginView(View):
    def get(self, request):
        if request.session.get("crm_user_id"):
            return redirect(request.GET.get("next") or "docs:index")

        bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "") or ""
        error = None
        if not bot_username:
            error = (
                "Конфигурация не завершена: переменная TELEGRAM_BOT_USERNAME не задана. "
                "Обратитесь к администратору."
            )
            logger.error("TELEGRAM_BOT_USERNAME is not set — docs login widget will not work")

        next_url = request.GET.get("next", reverse("docs:index"))
        auth_callback_url = request.build_absolute_uri(
            reverse("docs:auth_callback") + f"?next={next_url}"
        )
        return render(request, "docs/login.html", {
            "bot_username": bot_username,
            "auth_callback_url": auth_callback_url,
            "error": error,
        })


class DocsAuthCallbackView(View):
    def get(self, request):
        from apps.crm.services import verify_telegram_login, TelegramAuthError

        params = dict(request.GET)
        next_url = params.pop("next", [reverse("docs:index")])[0]
        flat = {k: v[0] if isinstance(v, list) else v for k, v in params.items()}

        try:
            data = verify_telegram_login(flat, settings.TELEGRAM_BOT_TOKEN)
        except TelegramAuthError as exc:
            logger.warning("Docs auth failed: %s", exc)
            bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "")
            return render(request, "docs/login.html", {
                "error": "Ошибка авторизации через Telegram. Попробуйте ещё раз.",
                "bot_username": bot_username,
            })

        telegram_id = int(data["id"])

        from apps.users.models import User
        user = User.objects.filter(telegram_id=telegram_id).first()

        if user is None:
            bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "")
            return render(request, "docs/login.html", {
                "error": (
                    "Вы не зарегистрированы в системе. "
                    "Сначала запустите бота — он зарегистрирует вас автоматически."
                ),
                "bot_username": bot_username,
            })

        request.session["crm_user_id"] = user.pk
        logger.info("Docs login: user %s (tg_id=%s)", user.display_name, telegram_id)
        return redirect(next_url)


class DocsLogoutView(View):
    def post(self, request):
        request.session.pop("crm_user_id", None)
        return redirect("docs:login")


# ─── Content views ────────────────────────────────────────────────────────────

class DocsIndexView(DocsLoginMixin, View):
    def get(self, request):
        return render(request, "docs/index.html", self.get_docs_context(request))


class DocsCRMView(DocsLoginMixin, View):
    def get(self, request):
        return render(request, "docs/crm.html", self.get_docs_context(request))


class DocsSpamControlView(DocsLoginMixin, View):
    def get(self, request):
        return render(request, "docs/spamcontrol.html", self.get_docs_context(request))


class DocsRatesView(DocsLoginMixin, View):
    def get(self, request):
        return render(request, "docs/rates.html", self.get_docs_context(request))


class DocsGuideView(DocsLoginMixin, View):
    def get(self, request):
        return render(request, "docs/guide.html", self.get_docs_context(request))


class DocsFAQView(DocsLoginMixin, View):
    def get(self, request):
        return render(request, "docs/faq.html", self.get_docs_context(request))


# ─── Spam app auth relay ──────────────────────────────────────────────────────
# Telegram Login Widget only works when served from the bot's registered domain.
# Bot domain = gramly.tech, but spam app lives on spam.gramly.tech.
# Flow: gramly.tech/spam-login/ → widget auth → gramly.tech/spam-auth/callback/
#       → call spam-backend for JWT → redirect spam.gramly.tech/auth/callback/?token=JWT

class SpamLoginView(View):
    """Show Telegram Login Widget on gramly.tech so the bot domain check passes."""

    def get(self, request):
        bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "")
        callback_url = request.build_absolute_uri(reverse("spam_auth_callback"))
        return render(request, "spam_login.html", {
            "bot_username": bot_username,
            "callback_url": callback_url,
        })


class SpamAuthCallbackView(View):
    """Receive Telegram auth data, exchange for JWT at spam-backend, redirect to spam app."""

    def get(self, request):
        tg_data = {k: v for k, v in request.GET.items()}

        spam_backend_url = getattr(settings, "SPAM_BACKEND_INTERNAL_URL", "http://spam-backend:8000")
        spam_app_url     = getattr(settings, "SPAM_APP_URL", "https://spam.gramly.tech")

        payload = json.dumps(tg_data).encode()
        req = urllib.request.Request(
            f"{spam_backend_url}/api/auth/telegram",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            logger.warning("SpamAuthRelay: spam-backend returned %s: %s", exc.code, body)
            return render(request, "spam_login.html", {
                "bot_username": getattr(settings, "TELEGRAM_BOT_USERNAME", ""),
                "callback_url": request.build_absolute_uri(reverse("spam_auth_callback")),
                "error": "Ошибка авторизации. Попробуйте ещё раз.",
            })
        except Exception as exc:
            logger.error("SpamAuthRelay: unexpected error: %s", exc)
            return render(request, "spam_login.html", {
                "bot_username": getattr(settings, "TELEGRAM_BOT_USERNAME", ""),
                "callback_url": request.build_absolute_uri(reverse("spam_auth_callback")),
                "error": "Сервис временно недоступен.",
            })

        jwt_token = result.get("access_token") or result.get("token", "")
        return redirect(f"{spam_app_url}/auth/callback/?token={urllib.parse.quote(jwt_token)}")
