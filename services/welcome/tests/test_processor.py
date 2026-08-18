from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gramly_welcome.models import InboxEvent
from gramly_welcome.processor import process_event


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
