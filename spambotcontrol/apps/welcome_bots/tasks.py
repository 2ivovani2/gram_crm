from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from asgiref.sync import async_to_sync
from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import Contact, GreetingDelivery, JoinRequest, ManagedBot
from .telegram_api import make_bot, send_saved_message

logger = logging.getLogger(__name__)


@shared_task(bind=True, autoretry_for=(), max_retries=5, ignore_result=True)
def send_greeting_task(self, delivery_id: int) -> None:
    with transaction.atomic():
        delivery = (
            GreetingDelivery.objects.select_for_update()
            .select_related("bot", "contact", "version", "channel")
            .filter(pk=delivery_id)
            .first()
        )
        if not delivery or delivery.status != GreetingDelivery.Status.SCHEDULED:
            return
        if not delivery.bot.is_active or not delivery.channel.is_active or not delivery.version:
            delivery.status = GreetingDelivery.Status.CANCELLED
            delivery.error = "Бот, канал или версия приветствия больше не активны"
            delivery.save(update_fields=("status", "error"))
            return

    bot = make_bot(delivery.bot.get_token())
    try:
        async_to_sync(send_saved_message)(bot, delivery.contact.telegram_id, delivery.version)
    except TelegramRetryAfter as exc:
        raise self.retry(countdown=max(1, int(exc.retry_after)))
    except TelegramAPIError as exc:
        _finish_delivery(delivery.id, success=False, error=str(exc))
    except Exception as exc:
        logger.exception("Welcome delivery %s failed", delivery.id)
        _finish_delivery(delivery.id, success=False, error=str(exc))
    else:
        _finish_delivery(delivery.id, success=True)
    finally:
        async_to_sync(bot.session.close)()


def _finish_delivery(delivery_id: int, *, success: bool, error: str = "") -> None:
    now = timezone.now()
    with transaction.atomic():
        delivery = GreetingDelivery.objects.select_for_update().select_related("contact").get(pk=delivery_id)
        delivery.status = GreetingDelivery.Status.SENT if success else GreetingDelivery.Status.FAILED
        delivery.sent_at = now
        delivery.error = error[:500]
        delivery.save(update_fields=("status", "sent_at", "error"))
        contact = delivery.contact
        contact.delivery_status = Contact.DeliveryStatus.LIVE if success else Contact.DeliveryStatus.DEAD
        contact.last_delivery_at = now
        contact.last_error = "" if success else error[:500]
        contact.save(update_fields=("delivery_status", "last_delivery_at", "last_error", "last_seen_at"))


@shared_task(bind=True, autoretry_for=(), max_retries=5, ignore_result=True)
def approve_join_request_task(self, request_id: int) -> None:
    with transaction.atomic():
        request = (
            JoinRequest.objects.select_for_update()
            .select_related("bot", "channel", "contact")
            .filter(pk=request_id)
            .first()
        )
        if not request or request.status != JoinRequest.Status.SCHEDULED:
            return
        if not request.bot.is_active or not request.channel.is_active or not request.bot.auto_approve:
            request.status = JoinRequest.Status.CANCELLED
            request.processed_at = timezone.now()
            request.error = "Автопринятие отключено либо бот/канал неактивен"
            request.save(update_fields=("status", "processed_at", "error"))
            return

    bot = make_bot(request.bot.get_token())
    try:
        async_to_sync(bot.approve_chat_join_request)(request.channel.telegram_id, request.contact.telegram_id)
    except TelegramRetryAfter as exc:
        raise self.retry(countdown=max(1, int(exc.retry_after)))
    except TelegramAPIError as exc:
        _finish_approval(request.id, success=False, error=str(exc))
    except Exception as exc:
        logger.exception("Join request %s failed", request.id)
        _finish_approval(request.id, success=False, error=str(exc))
    else:
        _finish_approval(request.id, success=True)
    finally:
        async_to_sync(bot.session.close)()


def _finish_approval(request_id: int, *, success: bool, error: str = "") -> None:
    JoinRequest.objects.filter(pk=request_id, status=JoinRequest.Status.SCHEDULED).update(
        status=JoinRequest.Status.APPROVED if success else JoinRequest.Status.FAILED,
        processed_at=timezone.now(),
        error=error[:500],
    )


@shared_task(ignore_result=True)
def purge_processed_updates_task(days: int = 14) -> int:
    from datetime import timedelta
    from .models import ProcessedUpdate

    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = ProcessedUpdate.objects.filter(created_at__lt=cutoff).delete()
    return deleted


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
    ignore_result=True,
)
def process_customer_update_task(self, managed_bot_id: int, raw_update: dict) -> None:
    from aiogram.types import Update
    from .client_updates import process_customer_update

    bot = ManagedBot.objects.select_related("owner").filter(pk=managed_bot_id, is_active=True).first()
    if not bot:
        return
    async_to_sync(process_customer_update)(bot, Update.model_validate(raw_update))


@shared_task(bind=True, max_retries=5, ignore_result=True)
def finalize_welcome_album_task(self, draft_id: int) -> None:
    from datetime import timedelta
    from .models import WelcomeDraft
    from .services import finalize_album

    draft = WelcomeDraft.objects.select_related("bot", "owner").filter(pk=draft_id).first()
    if not draft:
        return
    if draft.updated_at > timezone.now() - timedelta(seconds=2):
        raise self.retry(countdown=2)
    owner_id = draft.owner.telegram_id
    bot_id = draft.bot_id
    version = finalize_album(draft_id)
    if not version:
        return

    async def notify_and_clear():
        from .interface import get_interface_bot, get_interface_dispatcher
        from .handlers import _one_button

        interface_bot = get_interface_bot()
        dp = await get_interface_dispatcher()
        context = dp.fsm.get_context(bot=interface_bot, chat_id=owner_id, user_id=owner_id)
        await context.clear()
        await interface_bot.send_message(
            owner_id,
            f"✅ Альбом сохранён · версия {version.version}",
            reply_markup=_one_button("⏱ Настроить задержку", f"show-wdelay:{bot_id}"),
        )

    async_to_sync(notify_and_clear)()
