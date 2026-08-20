from __future__ import annotations

from datetime import UTC, datetime, timedelta

JOIN_REQUEST_WINDOW_SECONDS = 300
JOIN_REQUEST_MAX_TIMELINE_SECONDS = 240
JOIN_REQUEST_APPROVAL_SAFETY_SECONDS = 5
ACTIVE_DELIVERY_STATUSES = frozenset({"scheduled", "processing"})


def message_window(event_date: object, *, now: datetime) -> tuple[datetime, datetime]:
    """Return Telegram's exact message expiry and our safe approval deadline."""
    try:
        if not isinstance(event_date, int | float | str):
            raise TypeError
        requested_at = datetime.fromtimestamp(int(event_date), UTC)
    except (TypeError, ValueError, OverflowError):
        requested_at = now
    expires_at = requested_at + timedelta(seconds=JOIN_REQUEST_WINDOW_SECONDS)
    return expires_at, safe_approval_deadline(expires_at)


def safe_approval_deadline(expires_at: datetime) -> datetime:
    return expires_at - timedelta(seconds=JOIN_REQUEST_APPROVAL_SAFETY_SECONDS)


def approval_action(
    *,
    auto_approve: bool,
    delivery_status: str | None,
    approval_deadline: datetime,
    now: datetime,
) -> str:
    if not auto_approve:
        return "pending"
    if delivery_status in ACTIVE_DELIVERY_STATUSES:
        return "wait" if now < approval_deadline else "cancel_then_approve"
    return "approve"
