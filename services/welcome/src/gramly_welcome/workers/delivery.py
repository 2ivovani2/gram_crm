from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
import socket
import uuid
from datetime import UTC, datetime
from functools import partial
from time import monotonic
from typing import Any

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from prometheus_client import start_http_server
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select, update

from ..audience import (
    claim_client_messages,
    defer_client_message,
    finish_client_message,
    load_client_message,
)
from ..bot_diagnostics import record_incident
from ..broadcasts import (
    claim_broadcast_recipients,
    defer_broadcast_recipient,
    finish_broadcast_recipient,
    load_broadcast_context,
)
from ..commercial import access_for_owner
from ..config import Settings, get_settings
from ..crypto import TokenDecryptionError, TokenKeyring
from ..db import session_factory
from ..flow_delivery import (
    cancel_delivery_operations,
    claim_operation_batch,
    defer_operation,
    finish_operation,
    load_operation_context,
)
from ..join_activation import (
    claim_activation_batch,
    defer_activation,
    finish_activation_message,
    load_activation_context,
)
from ..join_request_policy import approval_action, safe_approval_deadline
from ..metrics import (
    DELIVERY_ATTEMPTS,
    DELIVERY_INFLIGHT,
    DELIVERY_SCHEDULE_LAG,
    DEPENDENCY_ERRORS,
    JOIN_REQUEST_ACTIVATIONS,
    OLDEST_PENDING_AGE,
    QUEUE_DEPTH,
    ROTATION_RECOMMENDATIONS,
    WORKER_ACTIVE,
)
from ..models import (
    BroadcastRecipient,
    ClientMessage,
    Contact,
    DeliveryOperation,
    FlowDelivery,
    GreetingDelivery,
    JoinRequest,
    JoinRequestActivation,
    RotationRecommendation,
)
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
    finish_join_request_terminal,
    load_delivery_context,
    load_join_request_context,
    pause_join_request,
)
from ..rotation import (
    claim_rotation_batch,
    defer_rotation,
    eligible_rotation_destinations,
    finish_rotation,
    load_rotation_context,
    mark_rotation_channel_error,
    store_rotation_invite_link,
)
from ..storage import MediaTooLargeError, ObjectStorage, ObjectStorageError
from ..telegram_client import bot_client, telegram_clients
from ..telegram_delivery import (
    approve_join_request,
    create_rotation_invite_link,
    send_client_notice,
    send_compiled_operation,
    send_delivery_operation,
    send_greeting,
    send_join_activation,
    send_rotation_recommendation,
)
from ..telegram_errors import normalize_bad_request, normalize_delivery_error
from .health import WorkerHealth
from .leased import run_leased

logger = logging.getLogger(__name__)


async def refresh_delivery_metrics() -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        actionable = or_(
            and_(
                DeliveryOperation.status.in_(("scheduled", "retry")),
                DeliveryOperation.due_at <= now,
            ),
            and_(
                DeliveryOperation.status == "processing",
                DeliveryOperation.lease_expires_at < now,
            ),
        )
        size, oldest = (
            await session.execute(
                select(func.count(DeliveryOperation.id), func.min(DeliveryOperation.due_at)).where(
                    actionable
                )
            )
        ).one()
    QUEUE_DEPTH.labels("delivery_operation", "actionable").set(int(size or 0))
    age = max(0.0, (now - oldest).total_seconds()) if oldest is not None else 0.0
    OLDEST_PENDING_AGE.labels("delivery_operation").set(age)


def _retry_delay(attempts: int) -> int:
    cap = int(min(300, 2 ** min(attempts, 8)))
    return random.randint(1, max(1, cap))


async def _record_telegram_cooldown(
    limiter: TelegramRateLimiter, bot_id: int, retry_after: int
) -> None:
    try:
        await limiter.block_bot(bot_id, max(1, retry_after))
    except RateLimitUnavailable:
        # The Telegram retry_after remains authoritative for the current job;
        # a missing distributed cooldown must not strand its database lease.
        DEPENDENCY_ERRORS.labels("valkey").inc()


async def _process_client_message(
    message_id: int,
    worker_id: str,
    settings: Settings,
    limiter: TelegramRateLimiter,
    keyring: TokenKeyring,
) -> None:
    async with session_factory() as session:
        context = await load_client_message(session, message_id)
    if context is None:
        async with session_factory() as session:
            await finish_client_message(
                session, message_id, worker_id, success=False, error="context_missing"
            )
        return
    try:
        allowed = await limiter.acquire(
            context.bot.id, context.contact.telegram_id, lane="interactive"
        )
    except RateLimitUnavailable:
        allowed = False
    if not allowed:
        async with session_factory() as session:
            await defer_client_message(
                session, message_id, worker_id, delay_seconds=5, error="rate_limited"
            )
        return
    try:
        await send_client_notice(context, keyring)
    except TelegramRetryAfter as exc:
        await _record_telegram_cooldown(limiter, context.bot.id, int(exc.retry_after))
        async with session_factory() as session:
            await defer_client_message(
                session, message_id, worker_id,
                delay_seconds=max(1, int(exc.retry_after)), error="telegram_429",
            )
    except (TelegramForbiddenError, TelegramBadRequest, TelegramUnauthorizedError, TokenDecryptionError) as exc:
        async with session_factory() as session:
            if isinstance(exc, TelegramForbiddenError):
                await session.execute(
                    update(Contact).where(Contact.id == context.contact.id).values(
                        consent_status="blocked", delivery_status="dead", last_error="bot_blocked_by_user"
                    )
                )
            await finish_client_message(
                session, message_id, worker_id, success=False, error=normalize_delivery_error(exc)
            )
    except (TelegramAPIError, OSError) as exc:
        if context.message.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_client_message(
                    session, message_id, worker_id, success=False, error=normalize_delivery_error(exc)
                )
        else:
            async with session_factory() as session:
                await defer_client_message(
                    session, message_id, worker_id,
                    delay_seconds=_retry_delay(context.message.attempts), error=normalize_delivery_error(exc),
                )
    else:
        async with session_factory() as session:
            await finish_client_message(session, message_id, worker_id, success=True)


async def _process_broadcast_recipient(
    recipient_id: int,
    worker_id: str,
    settings: Settings,
    limiter: TelegramRateLimiter,
    keyring: TokenKeyring,
    storage: ObjectStorage,
) -> None:
    async with session_factory() as session:
        context = await load_broadcast_context(session, recipient_id)
    if context is None:
        async with session_factory() as session:
            await finish_broadcast_recipient(
                session, recipient_id, worker_id, status="failed", error="context_missing"
            )
        return
    if context.contact.consent_status != "active":
        async with session_factory() as session:
            await finish_broadcast_recipient(
                session, recipient_id, worker_id, status="unsubscribed"
            )
        return
    try:
        allowed = await limiter.acquire(context.bot.id, context.contact.telegram_id, lane="bulk")
    except RateLimitUnavailable:
        allowed = False
    if not allowed:
        async with session_factory() as session:
            await defer_broadcast_recipient(
                session, recipient_id, worker_id, delay_seconds=5, error="rate_limited"
            )
        return
    try:
        token = keyring.decrypt(context.bot.token_ciphertext)
        async with bot_client(token) as bot:
            for operation in context.operations:
                await send_compiled_operation(
                    bot,
                    context.contact.telegram_id,
                    operation.operation_type,
                    operation.payload,
                    operation.media,
                    storage,
                    first_name=context.contact.first_name,
                )
    except TelegramRetryAfter as exc:
        await _record_telegram_cooldown(limiter, context.bot.id, int(exc.retry_after))
        async with session_factory() as session:
            await defer_broadcast_recipient(
                session, recipient_id, worker_id,
                delay_seconds=max(1, int(exc.retry_after)), error="telegram_429",
            )
    except TelegramForbiddenError:
        async with session_factory() as session:
            await finish_broadcast_recipient(
                session, recipient_id, worker_id, status="blocked", error="bot_blocked_by_user"
            )
    except (TelegramBadRequest, TelegramUnauthorizedError, TokenDecryptionError) as exc:
        async with session_factory() as session:
            await finish_broadcast_recipient(
                session, recipient_id, worker_id, status="failed", error=normalize_delivery_error(exc)
            )
    except (TelegramAPIError, ObjectStorageError, OSError) as exc:
        if context.recipient.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_broadcast_recipient(
                    session, recipient_id, worker_id, status="failed", error=normalize_delivery_error(exc)
                )
        else:
            async with session_factory() as session:
                await defer_broadcast_recipient(
                    session, recipient_id, worker_id,
                    delay_seconds=_retry_delay(context.recipient.attempts), error=normalize_delivery_error(exc),
                )
    else:
        async with session_factory() as session:
            await finish_broadcast_recipient(session, recipient_id, worker_id, status="sent")


async def _process_activation(
    activation_id: int,
    worker_id: str,
    settings: Settings,
    limiter: TelegramRateLimiter,
    keyring: TokenKeyring,
) -> None:
    async with session_factory() as session:
        context = await load_activation_context(session, activation_id=activation_id)
    if context is None:
        async with session_factory() as session:
            await finish_activation_message(
                session, activation_id, worker_id, success=False, error="activation_context_missing"
            )
        JOIN_REQUEST_ACTIVATIONS.labels("failed").inc()
        return
    now = datetime.now(UTC)
    if context.request.status in {"joined", "approved", "failed"}:
        async with session_factory() as session:
            await session.execute(update(JoinRequestActivation).where(
                JoinRequestActivation.id == activation_id, JoinRequestActivation.lease_owner == worker_id,
            ).values(status="approved_without_activation", lease_owner=None, lease_expires_at=None,
                     error="join_request_already_processed"))
            await session.commit()
        return
    if (
        context.request.user_chat_id is None
        or context.request.message_window_expires_at is None
        or context.request.message_window_expires_at <= now
    ):
        async with session_factory() as session:
            await finish_activation_message(
                session, activation_id, worker_id, success=False, error="temporary_chat_expired"
            )
        JOIN_REQUEST_ACTIVATIONS.labels("expired").inc()
        return
    try:
        allowed = await limiter.acquire(context.bot.id, context.request.user_chat_id)
    except RateLimitUnavailable:
        DEPENDENCY_ERRORS.labels("valkey").inc()
        allowed, delay, reason = False, 5, "valkey_unavailable"
    else:
        delay, reason = 1, "rate_limited"
    if not allowed:
        async with session_factory() as session:
            await defer_activation(
                session, activation_id, worker_id, delay_seconds=delay, error=reason
            )
        JOIN_REQUEST_ACTIVATIONS.labels("deferred").inc()
        return
    try:
        await send_join_activation(
            context, keyring, activation_base_url=settings.activation_base_url
        )
    except TelegramRetryAfter as exc:
        await _record_telegram_cooldown(limiter, context.bot.id, int(exc.retry_after))
        async with session_factory() as session:
            await defer_activation(
                session,
                activation_id,
                worker_id,
                delay_seconds=max(1, int(exc.retry_after)),
                error="telegram_429",
            )
        JOIN_REQUEST_ACTIVATIONS.labels("retry").inc()
    except (TelegramForbiddenError, TelegramBadRequest, TelegramUnauthorizedError, TokenDecryptionError, ValueError) as exc:
        async with session_factory() as session:
            if isinstance(exc, TelegramUnauthorizedError):
                await record_incident(session, context.bot.id, "invalid_bot_token")
            await finish_activation_message(
                session, activation_id, worker_id, success=False,
                error=normalize_delivery_error(exc, temporary=True)
            )
        JOIN_REQUEST_ACTIVATIONS.labels("failed").inc()
    except (TelegramAPIError, OSError) as exc:
        if context.activation.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_activation_message(
                    session, activation_id, worker_id, success=False, error=normalize_delivery_error(exc)
                )
            JOIN_REQUEST_ACTIVATIONS.labels("failed").inc()
        else:
            async with session_factory() as session:
                await defer_activation(
                    session,
                    activation_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.activation.attempts),
                    error=normalize_delivery_error(exc),
                )
            JOIN_REQUEST_ACTIVATIONS.labels("retry").inc()
    else:
        async with session_factory() as session:
            await finish_activation_message(session, activation_id, worker_id, success=True)
        JOIN_REQUEST_ACTIVATIONS.labels("sent").inc()


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
    if context.operation.status != "processing" or context.operation.lease_owner != worker_id:
        DELIVERY_ATTEMPTS.labels("operation_cancelled").inc()
        return
    if context.delivery.status in {"completed", "partial", "failed", "cancelled", "unreachable"}:
        async with session_factory() as session:
            await cancel_delivery_operations(
                session, context.delivery.id, reason="delivery_already_finished"
            )
        DELIVERY_ATTEMPTS.labels("operation_cancelled").inc()
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
    if context.target_expired:
        async with session_factory() as session:
            await cancel_delivery_operations(
                session,
                context.delivery.id,
                reason="join_request_window_expired",
            )
        DELIVERY_ATTEMPTS.labels("operation_cancelled").inc()
        return
    if "invalid_bot_token" in (context.bot.delivery_issues or {}):
        async with session_factory() as session:
            await finish_operation(session, operation_id, worker_id, success=False, error="invalid_bot_token")
        DELIVERY_ATTEMPTS.labels("operation_failed").inc()
        return
    try:
        allowed = await limiter.acquire(context.bot.id, context.target_chat_id)
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
        await _record_telegram_cooldown(limiter, context.bot.id, int(exc.retry_after))
        async with session_factory() as session:
            await defer_operation(
                session,
                operation_id,
                worker_id,
                delay_seconds=max(1, int(exc.retry_after)),
                error="telegram_429",
            )
        DELIVERY_ATTEMPTS.labels("telegram_429").inc()
    except (TelegramForbiddenError, TelegramBadRequest, TelegramUnauthorizedError,
            MediaTooLargeError, TokenDecryptionError) as exc:
        async with session_factory() as session:
            request_closed = bool(await session.scalar(select(JoinRequest.id).where(
                JoinRequest.welcome_delivery_id == context.delivery.id,
                JoinRequest.status.in_(("joined", "approved", "failed")),
            ).limit(1))) if context.delivery.target_chat_id is not None else False
            reason = normalize_delivery_error(exc, temporary=context.delivery.target_chat_id is not None,
                                              expired=context.target_expired, request_closed=request_closed)
            if isinstance(exc, TelegramUnauthorizedError):
                await record_incident(session, context.bot.id, "invalid_bot_token")
            # A membership update may have safely switched a known contact to
            # its permanent target while this request was in flight.
            latest = await session.get(FlowDelivery, context.delivery.id)
            if (request_closed and context.contact.bot_started and latest is not None
                    and latest.target_chat_id is None and latest.status in {"scheduled", "processing"}):
                await defer_operation(session, operation_id, worker_id, delay_seconds=1, error="target_changed")
                return
            await finish_operation(
                session,
                operation_id,
                worker_id,
                success=False,
                error=reason,
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
                    error=normalize_delivery_error(exc),
                )
            DELIVERY_ATTEMPTS.labels("operation_failed").inc()
        else:
            async with session_factory() as session:
                await defer_operation(
                    session,
                    operation_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.operation.attempts),
                    error=normalize_delivery_error(exc),
                )
            DELIVERY_ATTEMPTS.labels("operation_retry").inc()
    except Exception as exc:
        logger.warning(
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
                    error=normalize_delivery_error(exc),
                )
            DELIVERY_ATTEMPTS.labels("operation_failed").inc()
        else:
            async with session_factory() as session:
                await defer_operation(
                    session,
                    operation_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.operation.attempts),
                    error=normalize_delivery_error(exc),
                )
            DELIVERY_ATTEMPTS.labels("operation_retry").inc()
    else:
        async with session_factory() as session:
            await finish_operation(session, operation_id, worker_id, success=True)
        DELIVERY_SCHEDULE_LAG.observe(
            max(0.0, (datetime.now(UTC) - context.operation.due_at).total_seconds())
        )
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
            await defer_delivery(session, delivery_id, worker_id, delay_seconds=delay, error=reason)
        DELIVERY_ATTEMPTS.labels("deferred").inc()
        return
    try:
        await send_greeting(context, storage, keyring)
    except TelegramRetryAfter as exc:
        await _record_telegram_cooldown(limiter, context.bot.id, int(exc.retry_after))
        async with session_factory() as session:
            await defer_delivery(
                session,
                delivery_id,
                worker_id,
                delay_seconds=max(1, int(exc.retry_after)),
                error="telegram_429",
            )
        DELIVERY_ATTEMPTS.labels("telegram_429").inc()
    except (TelegramForbiddenError, TelegramBadRequest, TelegramUnauthorizedError, MediaTooLargeError, TokenDecryptionError) as exc:
        async with session_factory() as session:
            if isinstance(exc, TelegramUnauthorizedError):
                await record_incident(session, context.bot.id, "invalid_bot_token")
            await finish_delivery(session, delivery_id, worker_id, success=False, error=normalize_delivery_error(exc))
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
                    session, delivery_id, worker_id, success=False, error=normalize_delivery_error(exc)
                )
            DELIVERY_ATTEMPTS.labels("failed").inc()
        else:
            async with session_factory() as session:
                await defer_delivery(
                    session,
                    delivery_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.delivery.attempts),
                    error=normalize_delivery_error(exc),
                )
            DELIVERY_ATTEMPTS.labels("retry").inc()
    except Exception as exc:
        logger.warning("unexpected delivery failure id=%s type=%s", delivery_id, type(exc).__name__)
        if context.delivery.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_delivery(
                    session, delivery_id, worker_id, success=False, error=normalize_delivery_error(exc)
                )
            DELIVERY_ATTEMPTS.labels("failed").inc()
        else:
            async with session_factory() as session:
                await defer_delivery(
                    session,
                    delivery_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.delivery.attempts),
                    error=normalize_delivery_error(exc),
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
    if not context.bot.is_active or not context.channel.is_active:
        async with session_factory() as session:
            await finish_join_request(
                session, request_id, worker_id, success=False, error="bot_or_channel_inactive"
            )
        DELIVERY_ATTEMPTS.labels("approval_cancelled").inc()
        return
    if not context.bot.auto_approve:
        async with session_factory() as session:
            await pause_join_request(session, request_id, worker_id)
        DELIVERY_ATTEMPTS.labels("approval_cancelled").inc()
        return
    if context.request.welcome_delivery_id is not None:
        deadline = (
            safe_approval_deadline(context.request.message_window_expires_at)
            if context.request.message_window_expires_at is not None
            else datetime.now(UTC)
        )
        async with session_factory() as session:
            delivery = await session.get(FlowDelivery, context.request.welcome_delivery_id)
        action = approval_action(
            auto_approve=context.bot.auto_approve,
            delivery_status=delivery.status if delivery is not None else None,
            approval_deadline=deadline,
            now=datetime.now(UTC),
        )
        if action == "wait":
            async with session_factory() as session:
                await defer_join_request(
                    session,
                    request_id,
                    worker_id,
                    delay_seconds=2,
                    error="waiting_for_welcome_delivery",
                )
            DELIVERY_ATTEMPTS.labels("approval_waiting_delivery").inc()
            return
        if action == "cancel_then_approve":
            async with session_factory() as session:
                await cancel_delivery_operations(
                    session,
                    context.request.welcome_delivery_id,
                    reason="join_request_window_expired",
                )
    try:
        allowed = await limiter.acquire(context.bot.id, context.channel.telegram_id)
    except RateLimitUnavailable:
        DEPENDENCY_ERRORS.labels("valkey").inc()
        allowed, delay, reason = False, 5, "valkey_unavailable"
    else:
        delay, reason = 1, "rate_limited"
    if not allowed:
        async with session_factory() as session:
            await defer_join_request(session, request_id, worker_id, delay_seconds=delay, error=reason)
        DELIVERY_ATTEMPTS.labels("approval_deferred").inc()
        return
    try:
        await approve_join_request(context, keyring)
    except TelegramRetryAfter as exc:
        await _record_telegram_cooldown(limiter, context.bot.id, int(exc.retry_after))
        async with session_factory() as session:
            await defer_join_request(
                session,
                request_id,
                worker_id,
                delay_seconds=max(1, int(exc.retry_after)),
                error="telegram_429",
            )
        DELIVERY_ATTEMPTS.labels("telegram_429").inc()
    except TelegramBadRequest as exc:
        reason = normalize_bad_request(exc)
        status = "joined" if reason == "user_already_participant" else "failed"
        async with session_factory() as session:
            await finish_join_request_terminal(
                session,
                request_id,
                worker_id,
                status=status,
                error=reason,
            )
        DELIVERY_ATTEMPTS.labels(
            "approval_already_joined" if status == "joined" else "approval_failed"
        ).inc()
    except (TelegramForbiddenError, TelegramUnauthorizedError, TokenDecryptionError) as exc:
        async with session_factory() as session:
            if isinstance(exc, TelegramUnauthorizedError):
                await record_incident(session, context.bot.id, "invalid_bot_token")
            await finish_join_request(
                session, request_id, worker_id, success=False, error=normalize_delivery_error(exc)
            )
        DELIVERY_ATTEMPTS.labels("approval_failed").inc()
    except (TelegramAPIError, OSError) as exc:
        if context.request.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_join_request(
                    session, request_id, worker_id, success=False, error=normalize_delivery_error(exc)
                )
            DELIVERY_ATTEMPTS.labels("approval_failed").inc()
        else:
            async with session_factory() as session:
                await defer_join_request(
                    session,
                    request_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.request.attempts),
                    error=normalize_delivery_error(exc),
                )
            DELIVERY_ATTEMPTS.labels("approval_retry").inc()
    except Exception as exc:
        logger.warning("unexpected join approval failure id=%s type=%s", request_id, type(exc).__name__)
        if context.request.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_join_request(
                    session, request_id, worker_id, success=False, error=normalize_delivery_error(exc)
                )
            DELIVERY_ATTEMPTS.labels("approval_failed").inc()
        else:
            async with session_factory() as session:
                await defer_join_request(
                    session,
                    request_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.request.attempts),
                    error=normalize_delivery_error(exc),
                )
            DELIVERY_ATTEMPTS.labels("approval_retry").inc()
    else:
        async with session_factory() as session:
            await finish_join_request(session, request_id, worker_id, success=True)
            await session.execute(
                update(JoinRequestActivation)
                .where(
                    JoinRequestActivation.join_request_id == request_id,
                    JoinRequestActivation.granted_at.is_(None),
                )
                .values(status="approved_without_activation")
            )
            await session.commit()
        JOIN_REQUEST_ACTIVATIONS.labels("approved_without_activation").inc()
        DELIVERY_ATTEMPTS.labels("approved").inc()


async def _process_rotation(
    recommendation_id: int,
    worker_id: str,
    settings: Settings,
    limiter: TelegramRateLimiter,
    keyring: TokenKeyring,
) -> None:
    async with session_factory() as session:
        context = await load_rotation_context(session, recommendation_id)
        access = (
            await access_for_owner(session, context.departure.owner_id) if context is not None else None
        )
        destinations = (
            await eligible_rotation_destinations(session, context)
            if context is not None and access is not None and access.entitlements.get("rotation", False)
            else []
        )
    if context is None:
        ROTATION_RECOMMENDATIONS.labels("context_missing").inc()
        return
    if not context.source_bot.is_active or not context.contact.bot_started:
        async with session_factory() as session:
            await finish_rotation(
                session, context, worker_id, status="unreachable", error="contact_not_reachable"
            )
        ROTATION_RECOMMENDATIONS.labels("unreachable").inc()
        return
    if access is None or not access.entitlements.get("rotation", False):
        async with session_factory() as session:
            await finish_rotation(
                session, context, worker_id, status="ineligible", error="business_required"
            )
        ROTATION_RECOMMENDATIONS.labels("ineligible").inc()
        return
    try:
        allowed = await limiter.acquire(
            context.source_bot.id, context.contact.telegram_id, lane="bulk"
        )
    except RateLimitUnavailable:
        DEPENDENCY_ERRORS.labels("valkey").inc()
        allowed, delay, reason = False, 5, "valkey_unavailable"
    else:
        delay, reason = 1, "rate_limited"
    if not allowed:
        async with session_factory() as session:
            await defer_rotation(
                session,
                recommendation_id,
                worker_id,
                delay_seconds=delay,
                error=reason,
            )
        ROTATION_RECOMMENDATIONS.labels("deferred").inc()
        return
    usable = []
    try:
        for destination in destinations:
            if not destination.rotation.invite_link:
                try:
                    invite_link = await create_rotation_invite_link(destination, keyring)
                except (TelegramForbiddenError, TelegramBadRequest, TelegramUnauthorizedError) as exc:
                    async with session_factory() as session:
                        if isinstance(exc, TelegramUnauthorizedError):
                            await record_incident(session, destination.bot.id, "invalid_bot_token")
                        await mark_rotation_channel_error(
                            session, destination.rotation.id, normalize_delivery_error(exc)
                        )
                    continue
                destination.rotation.invite_link = invite_link
                async with session_factory() as session:
                    await store_rotation_invite_link(session, destination.rotation.id, invite_link)
            usable.append(destination)
        if not usable:
            async with session_factory() as session:
                await finish_rotation(
                    session, context, worker_id, status="ineligible", error="no_eligible_channels"
                )
            ROTATION_RECOMMENDATIONS.labels("empty").inc()
            return
        await send_rotation_recommendation(context, usable, keyring)
    except TelegramRetryAfter as exc:
        await _record_telegram_cooldown(
            limiter, context.source_bot.id, int(exc.retry_after)
        )
        async with session_factory() as session:
            await defer_rotation(
                session,
                recommendation_id,
                worker_id,
                delay_seconds=max(1, int(exc.retry_after)),
                error="telegram_429",
            )
        ROTATION_RECOMMENDATIONS.labels("telegram_429").inc()
    except (TelegramForbiddenError, TelegramBadRequest, TelegramUnauthorizedError, TokenDecryptionError) as exc:
        async with session_factory() as session:
            if isinstance(exc, TelegramUnauthorizedError):
                await record_incident(session, context.source_bot.id, "invalid_bot_token")
            await finish_rotation(
                session, context, worker_id, status="failed", error=normalize_delivery_error(exc)
            )
        ROTATION_RECOMMENDATIONS.labels("failed").inc()
    except (TelegramAPIError, OSError) as exc:
        if context.recommendation.attempts >= settings.max_attempts:
            async with session_factory() as session:
                await finish_rotation(
                    session, context, worker_id, status="failed", error=normalize_delivery_error(exc)
                )
            ROTATION_RECOMMENDATIONS.labels("failed").inc()
        else:
            async with session_factory() as session:
                await defer_rotation(
                    session,
                    recommendation_id,
                    worker_id,
                    delay_seconds=_retry_delay(context.recommendation.attempts),
                    error=normalize_delivery_error(exc),
                )
            ROTATION_RECOMMENDATIONS.labels("retry").inc()
    else:
        async with session_factory() as session:
            await finish_rotation(
                session, context, worker_id, status="sent", destinations=usable
            )
        ROTATION_RECOMMENDATIONS.labels("sent").inc()


async def serve() -> None:
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event_signal, stopping.set)
    redis = Redis.from_url(
        settings.valkey_url,
        decode_responses=False,
        socket_timeout=0.2,
        socket_connect_timeout=0.2,
        health_check_interval=15,
    )
    limiter = TelegramRateLimiter(
        redis,
        bot_limit=settings.bot_rate_limit_per_second,
        chat_limit=settings.chat_rate_limit_per_second,
        urgent_reserve=settings.urgent_rate_reserve_per_second,
    )
    storage = ObjectStorage(settings)
    keyring = TokenKeyring.parse(settings.token_encryption_keys)
    WORKER_ACTIVE.labels("delivery").inc()
    health = WorkerHealth({"delivery": 150})
    server = health.start()
    next_metrics_refresh = 0.0
    common: dict[str, Any] = {"worker_id": worker_id, "settings": settings, "limiter": limiter, "keyring": keyring}
    # Short-lived join-request targets go first. Broadcast recipients have no
    # Telegram delivery window and are deliberately handled after greetings.
    enabled_lanes = {lane.strip() for lane in settings.delivery_lanes.split(",") if lane.strip()}
    queues: list[tuple[str, Any, Any, Any]] = [
        (
            "urgent", DeliveryOperation, claim_operation_batch,
            partial(_process_operation, storage=storage, **common),
        ),
        (
            "urgent", GreetingDelivery, claim_delivery_batch,
            partial(_process_delivery, storage=storage, **common),
        ),
        ("urgent", JoinRequest, claim_join_request_batch, partial(_process_join_request, **common)),
        (
            "interactive", ClientMessage, claim_client_messages,
            partial(_process_client_message, **common),
        ),
        (
            "urgent", JoinRequestActivation, claim_activation_batch,
            partial(_process_activation, **common),
        ),
        (
            "bulk", BroadcastRecipient,
            claim_broadcast_recipients,
            partial(_process_broadcast_recipient, storage=storage, **common),
        ),
        (
            "bulk", RotationRecommendation, claim_rotation_batch,
            partial(_process_rotation, **common),
        ),
    ]
    queues = [queue for queue in queues if queue[0] in enabled_lanes]
    in_flight: set[asyncio.Task[None]] = set()
    try:
        while not stopping.is_set():
            health.mark("delivery")
            if "urgent" in enabled_lanes and monotonic() >= next_metrics_refresh:
                try:
                    await refresh_delivery_metrics()
                except Exception as exc:
                    logger.warning("Delivery metrics refresh failed class=%s", type(exc).__name__)
                next_metrics_refresh = monotonic() + 10
            finalized_albums: list[tuple[int, int, int, int]] = []
            if "urgent" in enabled_lanes:
                try:
                    async with session_factory() as session:
                        finalized_albums = await finalize_due_albums(session)
                except Exception as exc:
                    health.mark("delivery", healthy=False)
                    logger.warning("Delivery dependency unavailable class=%s", type(exc).__name__)
                    await asyncio.sleep(2)
                    continue
            for draft_id, owner_telegram_id, bot_id, version in finalized_albums:
                health.mark("delivery")
                try:
                    context = interface_dispatcher().fsm.get_context(
                        bot=interface_bot(),
                        chat_id=owner_telegram_id,
                        user_id=owner_telegram_id,
                    )
                    # The old album notification must not erase a newer,
                    # PostgreSQL-backed composer session in the owner worker.
                    if not settings.separate_owner_worker:
                        await context.clear()
                    await interface_bot().send_message(
                        owner_telegram_id,
                        f"✅ Альбом сохранён · версия {version}",
                        reply_markup=_one_button("⏱ Настроить задержку", f"show-wdelay:{bot_id}"),
                    )
                    async with session_factory() as session:
                        await complete_album_notification(session, draft_id)
                except Exception as exc:
                    logger.warning("owner album notification failed draft_id=%s class=%s", draft_id, type(exc).__name__)
                    continue
            completed = {task for task in in_flight if task.done()}
            in_flight.difference_update(completed)
            for task in completed:
                try:
                    task.result()
                except Exception as exc:
                    health.mark("delivery", healthy=False)
                    logger.warning("Delivery task retry class=%s", type(exc).__name__)
            worked = bool(in_flight)
            DELIVERY_INFLIGHT.labels(",".join(sorted(enabled_lanes))).set(len(in_flight))
            available = settings.delivery_concurrency - len(in_flight)
            per_queue_quota = max(1, settings.delivery_concurrency // max(1, len(queues)))
            for _lane, model, claim, handler in queues:
                if stopping.is_set():
                    break
                if available <= 0:
                    break
                health.mark("delivery")
                try:
                    async with session_factory() as session:
                        items = await claim(
                            session,
                            worker_id=worker_id,
                            limit=min(available, settings.worker_batch_size, per_queue_quota),
                            lease_seconds=settings.lease_seconds,
                        )
                    for item in items:
                        worked = True
                        in_flight.add(
                            asyncio.create_task(
                                run_leased(handler(item.id), model, item.id, worker_id)
                            )
                        )
                        available -= 1
                    health.mark("delivery", healthy=True)
                except Exception as exc:
                    health.mark("delivery", healthy=False)
                    logger.warning("Delivery queue retry class=%s", type(exc).__name__)
                    # Failed/unacknowledged work remains leased in PostgreSQL;
                    # it is recovered, never erased by a loop-level failure.
                    await asyncio.sleep(1)
            if in_flight:
                await asyncio.wait(in_flight, timeout=0.1, return_when=asyncio.FIRST_COMPLETED)
            elif not worked:
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
    finally:
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
        DELIVERY_INFLIGHT.labels(",".join(sorted(enabled_lanes))).set(0)
        server.shutdown()
        WORKER_ACTIVE.labels("delivery").dec()
        await telegram_clients.close()
        await redis.aclose()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    start_http_server(9090)
    asyncio.run(serve())


if __name__ == "__main__":
    run()
