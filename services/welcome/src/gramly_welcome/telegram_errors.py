from __future__ import annotations

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
    "telegram_bad_request", "TelegramForbiddenError", "TelegramBadRequest",
}


def count_delivery_failure(reason: str) -> None:
    from .metrics import DELIVERY_FAILURE_REASONS
    scope = "client" if reason in CLIENT_FAILURES else "platform"
    known = CLIENT_FAILURES | {"object_storage_error", "token_decryption_error", "telegram_temporary_error",
                               "operation_context_missing", "delivery_context_missing", "internal_error"}
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
    )
    for reason, markers in known_reasons:
        if any(marker in message for marker in markers):
            return reason

    return "telegram_bad_request"
