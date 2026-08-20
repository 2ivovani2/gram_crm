from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gramly_welcome.content_service import flow_timeline_seconds
from gramly_welcome.flow_delivery import OperationContext
from gramly_welcome.join_request_policy import (
    JOIN_REQUEST_MAX_TIMELINE_SECONDS,
    approval_action,
    message_window,
)
from gramly_welcome.models import (
    Channel,
    Contact,
    ContentFlowVersion,
    ContentStep,
    DeliveryOperation,
    FlowDelivery,
    ManagedBot,
)


def test_join_request_window_keeps_four_minutes_for_flow_and_time_for_delivery() -> None:
    requested_at = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)

    expires_at, approval_deadline = message_window(
        int(requested_at.timestamp()),
        now=requested_at + timedelta(seconds=2),
    )

    assert JOIN_REQUEST_MAX_TIMELINE_SECONDS == 240
    assert expires_at == requested_at + timedelta(minutes=5)
    assert approval_deadline < expires_at
    assert approval_deadline >= requested_at + timedelta(seconds=240)


def test_invalid_telegram_date_uses_receipt_time() -> None:
    now = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)

    expires_at, _ = message_window("not-a-date", now=now)

    assert expires_at == now + timedelta(minutes=5)


def test_auto_approve_waits_for_delivery_but_manual_request_stays_pending() -> None:
    now = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
    deadline = now + timedelta(minutes=4)

    assert (
        approval_action(
            auto_approve=False,
            delivery_status="completed",
            approval_deadline=deadline,
            now=now,
        )
        == "pending"
    )
    assert (
        approval_action(
            auto_approve=True,
            delivery_status="processing",
            approval_deadline=deadline,
            now=now,
        )
        == "wait"
    )
    assert (
        approval_action(
            auto_approve=True,
            delivery_status="partial",
            approval_deadline=deadline,
            now=now,
        )
        == "approve"
    )
    assert (
        approval_action(
            auto_approve=True,
            delivery_status="processing",
            approval_deadline=deadline,
            now=deadline,
        )
        == "cancel_then_approve"
    )


def test_flow_timeline_counts_first_and_between_step_delays_only() -> None:
    version = ContentFlowVersion(first_delay_seconds=20)
    steps = [
        ContentStep(delay_after_seconds=40),
        ContentStep(delay_after_seconds=60),
        ContentStep(delay_after_seconds=9_999),
    ]

    assert flow_timeline_seconds(version, steps) == 120


def test_operation_context_prefers_temporary_join_request_chat() -> None:
    expires_at = datetime.now(UTC) + timedelta(minutes=1)
    context = OperationContext(
        operation=DeliveryOperation(),
        delivery=FlowDelivery(target_chat_id=987_654, target_expires_at=expires_at),
        bot=ManagedBot(),
        channel=Channel(),
        contact=Contact(telegram_id=123_456),
    )

    assert context.target_chat_id == 987_654
    assert context.target_expired is False
