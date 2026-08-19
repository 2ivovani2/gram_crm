from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from prometheus_client import start_http_server
from redis.asyncio import Redis

from ..config import Settings, get_settings
from ..crypto import TokenDecryptionError, TokenKeyring
from ..db import session_factory
from ..flow_delivery import (
    claim_operation_batch,
    defer_operation,
    finish_operation,
    load_operation_context,
)
from ..metrics import DELIVERY_ATTEMPTS, DEPENDENCY_ERRORS, WORKER_ACTIVE
from ..owner_bot import _one_button, interface_bot, interface_dispatcher
from ..owner_repository import complete_album_notification, finalize_due_albums
from ..rate_limit import RateLimitUnavailable, TelegramRateLimiter
from ..repository import (
    claim_delivery_batch,
    claim_join_request_batch,
    defer_delivery,
    defer_join_request,
    finish_delivery,
    finish_join_request,
    load_delivery_context,
    load_join_request_context,
)
from ..storage import MediaTooLargeError, ObjectStorage, ObjectStorageError
from ..telegram_delivery import approve_join_request, send_delivery_operation, send_greeting

logger = logging.getLogger(__name__)


def _retry_delay(attempts: int) -> int:
    return int(min(300, 2 ** min(attempts, 8)))


async def _process_operation(
    operation_id: int,
    worker_id: str,
    settings: Settings,
    limiter: TelegramRateLimiter,
    storage: ObjectStorage,
    keyring: TokenKeyring,
) -> None:
    async with session_factory() as session:
        context = await load_operation_context(session, operation_id)
    if context is None:
        async with session_factory() as session:
            await finish_operation(
                session,
                operation_id,
                worker_id,
                success=False,
                error="operation_context_missing",
            )
        DELIVERY_ATTEMPTS.labels("operation_failed").inc()
        return
    if not context.bot.is_active or not context.channel.is_active:
        async with session_factory() as session:
            await finish_operation(
                session,
                operation_id,
                worker_id,
                success=False,
                error="bot_or_channel_inactive",
            )
        DELIVERY_ATTEMPTS.labels("operation_cancelled").inc()
        return
    try:
        allowed = await limiter.acquire(context.bot.id, context.contact.telegram_id)
    except RateLimitUnavailable:
        DEPENDENCY_ERRORS.labels("valkey").inc()
        allowed, delay, reason = False, 5, "valkey_unavailable"
    else:
        delay, reason = 1, "rate_limited"
    if not allowed:
        async with session_factory() as session:
            await defer_operation(
                session,
                operation_id,
                worker_id,
                delay_seconds=delay,
                error=reason,
            )
        DELIVERY_ATTEMPTS.labels("operation_deferred").inc()
        return
    try:
        await send_delivery_operation(context, storage, keyring)
    except TelegramRetryAfter as exc:
        async with session_factory() as session:
            await defer_operation(
                session,
                operation_id,
                worker_id,
                delay_seconds=max(1, int(exc.retry_after)),
                error="telegram_429",
            )
        DELIVERY_ATTEMPTS.labels("telegram_429").inc()
    except (TelegramForbiddenError, TelegramBadRequest, MediaTooLargeError, TokenDecryptionError) as exc:
        async with session_factory() as session:
            await finish_operation(
                session,
                operation_id,
                worker_id,
                success=False,
                error=type(exc).__name__,
            )
        DELIVERY_ATTEMPTS.labels("operation_failed").inc()
    except ObjectStorageError:
        DEPENDENCY_ERRORS.labels("object_storage").inc()
        if context.operation.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_operation(
                    session,
                    operation_id,
                    worker_id,
                    success=False,
                    error="object_storage_error",
                )
            DELIVERY_ATTEMPTS.labels("operation_failed").inc()
        else:
            async with session_factory() as session:
                await defer_operation(
                    session,
                    operation_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.operation.attempts),
                    error="object_storage_error",
                )
            DELIVERY_ATTEMPTS.labels("operation_retry").inc()
    except (TelegramAPIError, OSError) as exc:
        if context.operation.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_operation(
                    session,
                    operation_id,
                    worker_id,
                    success=False,
                    error=type(exc).__name__,
                )
            DELIVERY_ATTEMPTS.labels("operation_failed").inc()
        else:
            async with session_factory() as session:
                await defer_operation(
                    session,
                    operation_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.operation.attempts),
                    error=type(exc).__name__,
                )
            DELIVERY_ATTEMPTS.labels("operation_retry").inc()
    except Exception as exc:
        logger.exception(
            "unexpected content operation failure id=%s type=%s",
            operation_id,
            type(exc).__name__,
        )
        if context.operation.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_operation(
                    session,
                    operation_id,
                    worker_id,
                    success=False,
                    error=type(exc).__name__,
                )
            DELIVERY_ATTEMPTS.labels("operation_failed").inc()
        else:
            async with session_factory() as session:
                await defer_operation(
                    session,
                    operation_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.operation.attempts),
                    error=type(exc).__name__,
                )
            DELIVERY_ATTEMPTS.labels("operation_retry").inc()
    else:
        async with session_factory() as session:
            await finish_operation(session, operation_id, worker_id, success=True)
        DELIVERY_ATTEMPTS.labels("operation_sent").inc()


async def _process_delivery(
    delivery_id: int,
    worker_id: str,
    settings: Settings,
    limiter: TelegramRateLimiter,
    storage: ObjectStorage,
    keyring: TokenKeyring,
) -> None:
    async with session_factory() as session:
        context = await load_delivery_context(session, delivery_id)
    if context is None:
        async with session_factory() as session:
            await finish_delivery(
                session, delivery_id, worker_id, success=False, error="delivery_context_missing"
            )
        DELIVERY_ATTEMPTS.labels("failed").inc()
        return
    if not context.bot.is_active or not context.channel.is_active:
        async with session_factory() as session:
            await finish_delivery(
                session, delivery_id, worker_id, success=False, error="bot_or_channel_inactive"
            )
        DELIVERY_ATTEMPTS.labels("cancelled").inc()
        return
    try:
        allowed = await limiter.acquire(context.bot.id, context.contact.telegram_id)
    except RateLimitUnavailable:
        DEPENDENCY_ERRORS.labels("valkey").inc()
        allowed, delay, reason = False, 5, "valkey_unavailable"
    else:
        delay, reason = 1, "rate_limited"
    if not allowed:
        async with session_factory() as session:
            await defer_delivery(
                session, delivery_id, worker_id, delay_seconds=delay, error=reason
            )
        DELIVERY_ATTEMPTS.labels("deferred").inc()
        return
    try:
        await send_greeting(context, storage, keyring)
    except TelegramRetryAfter as exc:
        async with session_factory() as session:
            await defer_delivery(
                session,
                delivery_id,
                worker_id,
                delay_seconds=max(1, int(exc.retry_after)),
                error="telegram_429",
            )
        DELIVERY_ATTEMPTS.labels("telegram_429").inc()
    except (TelegramForbiddenError, TelegramBadRequest, MediaTooLargeError, TokenDecryptionError) as exc:
        async with session_factory() as session:
            await finish_delivery(
                session, delivery_id, worker_id, success=False, error=type(exc).__name__
            )
        DELIVERY_ATTEMPTS.labels("failed").inc()
    except ObjectStorageError:
        DEPENDENCY_ERRORS.labels("object_storage").inc()
        if context.delivery.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_delivery(
                    session, delivery_id, worker_id, success=False, error="object_storage_error"
                )
            DELIVERY_ATTEMPTS.labels("failed").inc()
        else:
            async with session_factory() as session:
                await defer_delivery(
                    session,
                    delivery_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.delivery.attempts),
                    error="object_storage_error",
                )
            DELIVERY_ATTEMPTS.labels("retry").inc()
    except (TelegramAPIError, OSError) as exc:
        if context.delivery.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_delivery(
                    session, delivery_id, worker_id, success=False, error=type(exc).__name__
                )
            DELIVERY_ATTEMPTS.labels("failed").inc()
        else:
            async with session_factory() as session:
                await defer_delivery(
                    session,
                    delivery_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.delivery.attempts),
                    error=type(exc).__name__,
                )
            DELIVERY_ATTEMPTS.labels("retry").inc()
    except Exception as exc:
        logger.exception("unexpected delivery failure id=%s type=%s", delivery_id, type(exc).__name__)
        if context.delivery.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_delivery(
                    session, delivery_id, worker_id, success=False, error=type(exc).__name__
                )
            DELIVERY_ATTEMPTS.labels("failed").inc()
        else:
            async with session_factory() as session:
                await defer_delivery(
                    session,
                    delivery_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.delivery.attempts),
                    error=type(exc).__name__,
                )
            DELIVERY_ATTEMPTS.labels("retry").inc()
    else:
        async with session_factory() as session:
            await finish_delivery(session, delivery_id, worker_id, success=True)
        DELIVERY_ATTEMPTS.labels("sent").inc()


async def _process_join_request(
    request_id: int,
    worker_id: str,
    settings: Settings,
    limiter: TelegramRateLimiter,
    keyring: TokenKeyring,
) -> None:
    async with session_factory() as session:
        context = await load_join_request_context(session, request_id)
    if context is None:
        async with session_factory() as session:
            await finish_join_request(
                session, request_id, worker_id, success=False, error="join_context_missing"
            )
        DELIVERY_ATTEMPTS.labels("approval_failed").inc()
        return
    if not context.bot.is_active or not context.bot.auto_approve or not context.channel.is_active:
        async with session_factory() as session:
            await finish_join_request(
                session, request_id, worker_id, success=False, error="approval_disabled"
            )
        DELIVERY_ATTEMPTS.labels("approval_cancelled").inc()
        return
    try:
        allowed = await limiter.acquire(context.bot.id, context.channel.telegram_id)
    except RateLimitUnavailable:
        DEPENDENCY_ERRORS.labels("valkey").inc()
        allowed, delay, reason = False, 5, "valkey_unavailable"
    else:
        delay, reason = 1, "rate_limited"
    if not allowed:
        async with session_factory() as session:
            await defer_join_request(
                session, request_id, worker_id, delay_seconds=delay, error=reason
            )
        DELIVERY_ATTEMPTS.labels("approval_deferred").inc()
        return
    try:
        await approve_join_request(context, keyring)
    except TelegramRetryAfter as exc:
        async with session_factory() as session:
            await defer_join_request(
                session,
                request_id,
                worker_id,
                delay_seconds=max(1, int(exc.retry_after)),
                error="telegram_429",
            )
        DELIVERY_ATTEMPTS.labels("telegram_429").inc()
    except (TelegramForbiddenError, TelegramBadRequest, TokenDecryptionError) as exc:
        async with session_factory() as session:
            await finish_join_request(
                session, request_id, worker_id, success=False, error=type(exc).__name__
            )
        DELIVERY_ATTEMPTS.labels("approval_failed").inc()
    except (TelegramAPIError, OSError) as exc:
        if context.request.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_join_request(
                    session, request_id, worker_id, success=False, error=type(exc).__name__
                )
            DELIVERY_ATTEMPTS.labels("approval_failed").inc()
        else:
            async with session_factory() as session:
                await defer_join_request(
                    session,
                    request_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.request.attempts),
                    error=type(exc).__name__,
                )
            DELIVERY_ATTEMPTS.labels("approval_retry").inc()
    except Exception as exc:
        logger.exception(
            "unexpected join approval failure id=%s type=%s", request_id, type(exc).__name__
        )
        if context.request.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_join_request(
                    session, request_id, worker_id, success=False, error=type(exc).__name__
                )
            DELIVERY_ATTEMPTS.labels("approval_failed").inc()
        else:
            async with session_factory() as session:
                await defer_join_request(
                    session,
                    request_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.request.attempts),
                    error=type(exc).__name__,
                )
            DELIVERY_ATTEMPTS.labels("approval_retry").inc()
    else:
        async with session_factory() as session:
            await finish_join_request(session, request_id, worker_id, success=True)
        DELIVERY_ATTEMPTS.labels("approved").inc()


async def serve() -> None:
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event_signal, stopping.set)
    redis = Redis.from_url(settings.valkey_url, decode_responses=False)
    limiter = TelegramRateLimiter(
        redis,
        bot_limit=settings.bot_rate_limit_per_second,
        chat_limit=settings.chat_rate_limit_per_second,
    )
    storage = ObjectStorage(settings)
    keyring = TokenKeyring.parse(settings.token_encryption_keys)
    WORKER_ACTIVE.labels("delivery").inc()
    try:
        while not stopping.is_set():
            async with session_factory() as session:
                finalized_albums = await finalize_due_albums(session)
            for draft_id, owner_telegram_id, bot_id, version in finalized_albums:
                try:
                    context = interface_dispatcher().fsm.get_context(
                        bot=interface_bot(),
                        chat_id=owner_telegram_id,
                        user_id=owner_telegram_id,
                    )
                    await context.clear()
                    await interface_bot().send_message(
                        owner_telegram_id,
                        f"✅ Альбом сохранён · версия {version}",
                        reply_markup=_one_button(
                            "⏱ Настроить задержку", f"show-wdelay:{bot_id}"
                        ),
                    )
                except Exception:
                    logger.exception("owner album notification failed draft_id=%s", draft_id)
                    continue
                async with session_factory() as session:
                    await complete_album_notification(session, draft_id)
            async with session_factory() as session:
                operations = await claim_operation_batch(
                    session,
                    worker_id=worker_id,
                    limit=settings.worker_batch_size,
                    lease_seconds=settings.lease_seconds,
                )
            async with session_factory() as session:
                deliveries = await claim_delivery_batch(
                    session,
                    worker_id=worker_id,
                    limit=settings.worker_batch_size,
                    lease_seconds=settings.lease_seconds,
                )
            async with session_factory() as session:
                join_requests = await claim_join_request_batch(
                    session,
                    worker_id=worker_id,
                    limit=settings.worker_batch_size,
                    lease_seconds=settings.lease_seconds,
                )
            if not operations and not deliveries and not join_requests:
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
                continue
            for operation in operations:
                if stopping.is_set():
                    break
                await _process_operation(
                    operation.id, worker_id, settings, limiter, storage, keyring
                )
            for delivery in deliveries:
                if stopping.is_set():
                    break
                await _process_delivery(
                    delivery.id, worker_id, settings, limiter, storage, keyring
                )
            for request in join_requests:
                if stopping.is_set():
                    break
                await _process_join_request(request.id, worker_id, settings, limiter, keyring)
    finally:
        WORKER_ACTIVE.labels("delivery").dec()
        await redis.aclose()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    start_http_server(9090)
    asyncio.run(serve())


if __name__ == "__main__":
    run()
