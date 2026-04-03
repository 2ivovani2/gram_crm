"""
Django async view that receives Telegram webhook updates.

Security: verifies X-Telegram-Bot-Api-Secret-Token header when
TELEGRAM_WEBHOOK_SECRET is set in settings.

Async view requires Django >= 4.1 + uvicorn (or any ASGI server).
"""
from __future__ import annotations
import hmac
import json
import logging
from django.conf import settings
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from aiogram.types import Update

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class TelegramWebhookView(View):
    """Receives Telegram updates and feeds them to the aiogram Dispatcher."""

    async def post(self, request, *args, **kwargs) -> JsonResponse:
        # ── Secret token verification ─────────────────────────────────────────
        if settings.TELEGRAM_WEBHOOK_SECRET:
            received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not hmac.compare_digest(received, settings.TELEGRAM_WEBHOOK_SECRET):
                logger.warning("Webhook: invalid secret token from %s", request.META.get("REMOTE_ADDR"))
                return HttpResponseForbidden("Invalid secret token")

        # ── Parse update ──────────────────────────────────────────────────────
        try:
            data = json.loads(request.body)
            update = Update.model_validate(data)
        except Exception as exc:
            logger.error("Webhook: failed to parse update: %s", exc)
            return HttpResponseBadRequest("Invalid update")

        # ── Feed to aiogram ───────────────────────────────────────────────────
        from apps.telegram_bot.bot import get_bot, get_dispatcher
        bot = get_bot()
        dp = await get_dispatcher()
        await dp.feed_update(bot=bot, update=update)

        return JsonResponse({"ok": True})
