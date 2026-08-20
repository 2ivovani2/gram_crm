from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from gramly_welcome.models import FeatureFlag, InboxEvent, ManagedBot
from gramly_welcome.processor import (
    _join_request_greetings_enabled,
    _join_request_upsert,
    membership_transition_flags,
    process_event,
)


def test_membership_transition_deduplicates_repeated_leave_state() -> None:
    assert membership_transition_flags(None, old_active=True, new_active=False) == (False, True)
    assert membership_transition_flags("left", old_active=True, new_active=False) == (False, False)


def test_membership_transition_allows_real_rejoin() -> None:
    assert membership_transition_flags("left", old_active=False, new_active=True) == (True, False)


@pytest.mark.asyncio
async def test_unsupported_bot_update_does_not_touch_business_tables() -> None:
    session = AsyncMock(spec=AsyncSession)
    event = cast(
        InboxEvent,
        SimpleNamespace(
            bot_id=42,
            update_id=1001,
            payload={"poll": {"id": "poll-1", "question": "ignored"}},
        ),
    )

    await process_event(session, event)

    session.get.assert_not_awaited()
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_interface_event_is_not_silently_discarded_before_cutover() -> None:
    session = AsyncMock(spec=AsyncSession)
    event = cast(
        InboxEvent,
        SimpleNamespace(bot_id=None, update_id=1002, payload={"poll": {"id": "poll-2"}}),
    )

    with pytest.raises(RuntimeError, match="interface consumer is not enabled"):
        await process_event(session, event)

    session.get.assert_not_awaited()
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_join_request_flag_supports_staged_bot_rollout() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = FeatureFlag(
        key="join_request_greetings",
        enabled=True,
        config={"bot_ids": [7, "9"]},
    )

    assert await _join_request_greetings_enabled(session, 7)
    assert await _join_request_greetings_enabled(session, 9)
    assert not await _join_request_greetings_enabled(session, 8)


def test_join_request_upsert_refreshes_an_existing_open_request() -> None:
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    bot = ManagedBot(
        id=8,
        auto_approve=False,
        approval_delay_seconds=0,
    )

    statement = _join_request_upsert(
        bot=bot,
        channel_id=14,
        contact_id=42,
        update_id=173472452,
        user_chat_id=987654,
        message_window_expires_at=now,
        due_at=None,
        now=now,
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]

    assert "ON CONFLICT (channel_id, contact_id)" in sql
    assert "DO UPDATE SET telegram_update_id = excluded.telegram_update_id" in sql
    assert "user_chat_id = excluded.user_chat_id" in sql
    assert "welcome_delivery_id = %(param_1)s" in sql
    assert "processed_at = %(param_" in sql
