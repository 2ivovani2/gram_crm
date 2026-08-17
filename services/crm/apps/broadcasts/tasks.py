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
    from apps.users.models import User
    from django.utils import timezone

    try:
        broadcast = Broadcast.objects.get(pk=broadcast_id)
    except Broadcast.DoesNotExist:
        logger.error("send_broadcast_task: Broadcast %d not found", broadcast_id)
        return

    if broadcast.status != BroadcastStatus.RUNNING:
        logger.warning(
            "send_broadcast_task: Broadcast %d is not RUNNING (status=%s)",
            broadcast_id,
            broadcast.status,
        )
        return

    # Load recipients synchronously before entering async context
    recipients = list(BroadcastService.get_recipients_queryset(broadcast).iterator(chunk_size=100))

    async def _deliver(users) -> tuple[list[int], list[tuple[int, str, str]]]:
        """
        Returns:
            blocked_tg_ids — telegram_ids of users who blocked the bot
            results        — list of (user_pk, DeliveryStatus, error_message)
        """
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
        from django.conf import settings

        bot = Bot(
            token=settings.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

        blocked_tg_ids: list[int] = []
        results: list[tuple[int, str, str]] = []

        try:
            for user in users:
                try:
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=broadcast.text,
                        parse_mode=broadcast.parse_mode,
                    )
                    results.append((user.pk, DeliveryStatus.SENT, ""))

                except TelegramForbiddenError:
                    blocked_tg_ids.append(user.telegram_id)
                    results.append((user.pk, DeliveryStatus.BLOCKED, "Bot blocked"))

                except TelegramRetryAfter as exc:
                    logger.warning("Rate limit hit, sleeping %ds", exc.retry_after)
                    await asyncio.sleep(exc.retry_after)
                    try:
                        await bot.send_message(
                            chat_id=user.telegram_id,
                            text=broadcast.text,
                            parse_mode=broadcast.parse_mode,
                        )
                        results.append((user.pk, DeliveryStatus.SENT, ""))
                    except Exception as inner_exc:
                        results.append((user.pk, DeliveryStatus.FAILED, str(inner_exc)))

                except Exception as exc:
                    logger.error("Failed to send to %d: %s", user.telegram_id, exc)
                    results.append((user.pk, DeliveryStatus.FAILED, str(exc)))

                await asyncio.sleep(SEND_DELAY_SECONDS)
        finally:
            await bot.session.close()

        return blocked_tg_ids, results

    blocked_tg_ids, results = asyncio.run(_deliver(recipients))

    # All ORM writes happen synchronously after asyncio.run() — safe in a sync Celery task
    if blocked_tg_ids:
        User.objects.filter(telegram_id__in=blocked_tg_ids).update(is_blocked_bot=True)

    # Build pk→user map for log_delivery (which expects a User object)
    user_by_pk = {u.pk: u for u in recipients}
    for user_pk, status, error in results:
        user = user_by_pk.get(user_pk)
        if user:
            BroadcastService.log_delivery(broadcast_id, user, status, error)

    Broadcast.objects.filter(pk=broadcast_id).update(
        status=BroadcastStatus.DONE,
        finished_at=timezone.now(),
    )
    logger.info("Broadcast %d completed: %d sent", broadcast_id, len(results))
