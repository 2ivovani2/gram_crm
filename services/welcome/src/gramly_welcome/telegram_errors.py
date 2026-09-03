from __future__ import annotations

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
