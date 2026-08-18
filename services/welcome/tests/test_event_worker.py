from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from gramly_welcome.models import InboxEvent
from gramly_welcome.workers import events as event_worker


@pytest.mark.asyncio
async def test_claimed_batch_respects_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    active = 0
    maximum = 0

    async def fake_process(_event: InboxEvent, _worker_id: str, _max_attempts: int) -> None:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1

    monkeypatch.setattr(event_worker, "process_claimed_event", fake_process)
    claimed = [
        cast(
            InboxEvent,
            SimpleNamespace(bot_id=None, payload={"message": {}}, id=index),
        )
        for index in range(12)
    ]

    await event_worker.process_claimed_batch(
        claimed,
        worker_id="worker:test",
        max_attempts=3,
        concurrency=4,
    )

    assert active == 0
    assert maximum == 4


@pytest.mark.asyncio
async def test_claimed_batch_bulk_completes_unsupported_client_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Transaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def begin(self) -> Transaction:
            return Transaction()

    bulk_finish = AsyncMock(return_value=2)
    process_one = AsyncMock()
    monkeypatch.setattr(event_worker, "session_factory", Session)
    monkeypatch.setattr(event_worker, "finish_inbox_events", bulk_finish)
    monkeypatch.setattr(event_worker, "process_claimed_event", process_one)
    ignored = [
        cast(InboxEvent, SimpleNamespace(bot_id=7, payload={"poll": {}}, id=1)),
        cast(InboxEvent, SimpleNamespace(bot_id=7, payload={"edited_channel_post": {}}, id=2)),
    ]
    actionable = cast(
        InboxEvent, SimpleNamespace(bot_id=7, payload={"message": {}}, id=3)
    )

    await event_worker.process_claimed_batch(
        [*ignored, actionable],
        worker_id="worker:test",
        max_attempts=3,
        concurrency=2,
    )

    bulk_finish.assert_awaited_once()
    finish_call = bulk_finish.await_args
    assert finish_call is not None
    assert finish_call.args[1] == [1, 2]
    process_one.assert_awaited_once_with(actionable, "worker:test", 3)
