from __future__ import annotations

import json
import logging

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)

CLIENT_FAILURES = {
    "invalid_bot_token", "bot_blocked_by_user", "join_request_window_expired", "join_request_already_processed",
    "temporary_address_unavailable", "write_access_unavailable", "bot_missing_approve_permission",
    "chat_not_found", "request_not_pending", "media_too_large", "bot_or_channel_inactive",
}
UNKNOWN_FAILURES = {"telegram_bad_request", "TelegramForbiddenError", "TelegramBadRequest"}
PLATFORM_FAILURES = {
    "object_storage_error", "token_decryption_error", "telegram_temporary_error",
    "operation_context_missing", "delivery_context_missing", "internal_error",
    "telegram_invalid_entities", "telegram_invalid_keyboard", "telegram_invalid_media",
    "telegram_invalid_text", "telegram_429",
}
DELIVERY_METHODS = {
    "sendMessage", "sendPhoto", "sendVideo", "sendAnimation", "sendDocument", "sendAudio",
    "sendVoice", "sendVideoNote", "sendSticker", "sendMediaGroup", "sendContact", "sendLocation",
    "sendVenue", "sendPoll", "sendDice", "copyMessage",
}


def log_delivery_failure(*, operation_id: int, delivery_id: int, method: str, reason: str) -> None:
    known = CLIENT_FAILURES | PLATFORM_FAILURES | UNKNOWN_FAILURES
    logging.getLogger(__name__).warning(json.dumps({
        "event": "delivery_operation_failed", "operation_id": operation_id, "delivery_id": delivery_id,
        "method": method if method in DELIVERY_METHODS else "other",
        "reason": reason if reason in known else "other", "scope": delivery_failure_scope(reason),
    }))


def delivery_failure_scope(reason: str) -> str:
    if reason in CLIENT_FAILURES:
        return "client"
    if reason in PLATFORM_FAILURES:
        return "platform"
    return "unknown"


def count_delivery_failure(reason: str) -> None:
    from .metrics import DELIVERY_FAILURE_REASONS
    scope = delivery_failure_scope(reason)
    known = CLIENT_FAILURES | PLATFORM_FAILURES | UNKNOWN_FAILURES
    DELIVERY_FAILURE_REASONS.labels(reason if reason in known else "other", scope).inc()


def normalize_delivery_error(exc: Exception, *, temporary: bool = False, expired: bool = False,
                             request_closed: bool = False) -> str:
    """Only known, bounded reason codes; never store provider text or PII."""
    if isinstance(exc, TelegramUnauthorizedError):
        return "invalid_bot_token"
    if type(exc).__name__ == "TokenDecryptionError":
        return "token_decryption_error"
    if type(exc).__name__ == "MediaTooLargeError":
        return "media_too_large"
    if expired:
        return "join_request_window_expired"
    if temporary and request_closed:
        return "join_request_already_processed"
    if isinstance(exc, TelegramRetryAfter):
        return "telegram_429"
    reason = normalize_bad_request(exc)
    if reason != "telegram_bad_request":
        return reason
    if isinstance(exc, TelegramForbiddenError):
        return "temporary_address_unavailable" if temporary else "write_access_unavailable"
    if isinstance(exc, (TimeoutError, OSError, TelegramNetworkError, TelegramServerError)):
        return "telegram_temporary_error"
    return "telegram_bad_request" if isinstance(exc, TelegramBadRequest) else "internal_error"


def normalize_bad_request(exc: Exception) -> str:
    """Return a stable, non-PII reason for a Telegram Bad Request."""
    message = str(getattr(exc, "message", "") or exc).casefold()
    known_reasons = (
        ("user_already_participant", ("user_already_participant", "already a participant")),
        ("request_not_pending", ("hide_requester_missing", "request not found")),
        ("bot_missing_approve_permission", ("chat_admin_required", "not enough rights")),
        ("bot_blocked_by_user", ("bot was blocked",)),
        ("chat_not_found", ("chat not found",)),
        ("telegram_invalid_entities", ("can't parse entities", "can't find end of the entity",
                                       "entity bounds", "entity offset")),
        ("telegram_invalid_keyboard", ("button_url_invalid", "button_data_invalid",
                                       "reply markup is too long", "can't parse reply keyboard markup",
                                       "inline keyboard expected", "text buttons are unallowed")),
        ("telegram_invalid_media", ("wrong file identifier", "wrong remote file identifier",
                                    "failed to get http url content", "media_empty", "photo_invalid_dimensions")),
        ("telegram_invalid_text", ("message text is empty", "message is too long", "caption is too long")),
    )
    for reason, markers in known_reasons:
        if any(marker in message for marker in markers):
            return reason

    return "telegram_bad_request"
