from __future__ import annotations

import hmac
import json
import logging

from aiogram.types import Update
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpResponseBadRequest, HttpResponseForbidden, HttpResponseNotFound, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .models import ManagedBot

logger = logging.getLogger(__name__)


def _header_valid(request, expected: str) -> bool:
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return bool(expected) and hmac.compare_digest(supplied, expected)


@method_decorator(csrf_exempt, name="dispatch")
class WelcomeInterfaceWebhookView(View):
    async def post(self, request, *args, **kwargs):
        if not settings.WELCOME_BOT_TOKEN:
            return HttpResponseNotFound()
        if not _header_valid(request, settings.WELCOME_WEBHOOK_SECRET):
            return HttpResponseForbidden("Invalid secret token")
        try:
            update = Update.model_validate(json.loads(request.body))
        except Exception:
            return HttpResponseBadRequest("Invalid update")
        from .interface import get_interface_bot, get_interface_dispatcher

        try:
            await (await get_interface_dispatcher()).feed_update(get_interface_bot(), update)
        except Exception:
            logger.exception("Unhandled Gramly Welcome interface update %s", update.update_id)
        return JsonResponse({"ok": True})


@method_decorator(csrf_exempt, name="dispatch")
class ClientBotWebhookView(View):
    async def post(self, request, public_id, path_secret, *args, **kwargs):
        try:
            managed_bot = await sync_to_async(
                ManagedBot.objects.select_related("owner").get
            )(public_id=public_id, is_active=True)
        except ManagedBot.DoesNotExist:
            return HttpResponseNotFound()
        if not hmac.compare_digest(path_secret, managed_bot.path_secret):
            return HttpResponseNotFound()
        if not _header_valid(request, managed_bot.webhook_secret):
            return HttpResponseForbidden("Invalid secret token")
        try:
            update = Update.model_validate(json.loads(request.body))
        except Exception:
            return HttpResponseBadRequest("Invalid update")
        try:
            from .tasks import process_customer_update_task

            await sync_to_async(process_customer_update_task.delay)(
                managed_bot.id,
                update.model_dump(mode="json", exclude_none=True),
            )
        except Exception:
            # Broker outage: return non-2xx so Telegram keeps the update and
            # retries instead of silently losing it.
            logger.exception("Could not enqueue customer bot update %s (%s)", update.update_id, public_id)
            return JsonResponse({"ok": False}, status=503)
        return JsonResponse({"ok": True})
