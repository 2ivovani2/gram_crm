"""
Celery tasks for broadcast delivery.

Rate limiting: ~20 messages/second (Telegram Bot API limit for non-premium bots).
Each user gets exactly one delivery attempt; TelegramForbiddenError marks user as bot-blocked.
"""
from __future__ import annotations
import asyncio
import logging
from celery import shared_task

logger = logging.getLogger(__name__)

SEND_DELAY_SECONDS = 0.05  # ~20 msg/sec


@shared_task(bind=True, max_retries=0, queue="broadcasts")
def send_broadcast_task(self, broadcast_id: int) -> None:
    from apps.broadcasts.models import Broadcast, BroadcastStatus, DeliveryStatus
    from apps.broadcasts.services import BroadcastService
    from apps.users.services import UserService
    from django.utils import timezone

    try:
        broadcast = Broadcast.objects.get(pk=broadcast_id)
    except Broadcast.DoesNotExist:
        logger.error("send_broadcast_task: Broadcast %d not found", broadcast_id)
        return

    if broadcast.status != BroadcastStatus.RUNNING:
        logger.warning("send_broadcast_task: Broadcast %d is not RUNNING (status=%s)", broadcast_id, broadcast.status)
        return

    async def _deliver() -> None:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
        from django.conf import settings

        bot = Bot(
            token=settings.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

        try:
            recipients = BroadcastService.get_recipients_queryset(broadcast)
            for user in recipients.iterator(chunk_size=100):
                try:
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=broadcast.text,
                        parse_mode=broadcast.parse_mode,
                    )
                    BroadcastService.log_delivery(broadcast_id, user, DeliveryStatus.SENT)

                except TelegramForbiddenError:
                    # User blocked the bot
                    UserService.mark_blocked_bot(user)
                    BroadcastService.log_delivery(broadcast_id, user, DeliveryStatus.BLOCKED, "Bot blocked")

                except TelegramRetryAfter as exc:
                    logger.warning("Rate limit hit, sleeping %ds", exc.retry_after)
                    await asyncio.sleep(exc.retry_after)
                    # Retry this user once
                    try:
                        await bot.send_message(
                            chat_id=user.telegram_id,
                            text=broadcast.text,
                            parse_mode=broadcast.parse_mode,
                        )
                        BroadcastService.log_delivery(broadcast_id, user, DeliveryStatus.SENT)
                    except Exception as inner_exc:
                        BroadcastService.log_delivery(broadcast_id, user, DeliveryStatus.FAILED, str(inner_exc))

                except Exception as exc:
                    logger.error("Failed to send to %d: %s", user.telegram_id, exc)
                    BroadcastService.log_delivery(broadcast_id, user, DeliveryStatus.FAILED, str(exc))

                await asyncio.sleep(SEND_DELAY_SECONDS)
        finally:
            await bot.session.close()

    asyncio.run(_deliver())

    Broadcast.objects.filter(pk=broadcast_id).update(
        status=BroadcastStatus.DONE,
        finished_at=timezone.now(),
    )
    logger.info("Broadcast %d completed", broadcast_id)
