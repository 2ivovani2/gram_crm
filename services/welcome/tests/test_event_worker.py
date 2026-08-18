from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from gramly_welcome.models import InboxEvent
from gramly_welcome.workers import events as event_worker


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
            SimpleNamespace(bot_id=7, payload={"message": {}}, id=index, update_id=index),
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
async def test_claimed_batch_preserves_owner_bot_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processed: list[int] = []

    async def fake_process(event: InboxEvent, _worker_id: str, _max_attempts: int) -> None:
        processed.append(event.update_id)

    monkeypatch.setattr(event_worker, "process_claimed_event", fake_process)
    claimed = [
        cast(
            InboxEvent,
            SimpleNamespace(
                bot_id=None,
                payload={"message": {}},
                id=index,
                update_id=update_id,
            ),
        )
        for index, update_id in enumerate((30, 10, 20))
    ]

    await event_worker.process_claimed_batch(
        claimed,
        worker_id="worker:test",
        max_attempts=3,
        concurrency=4,
    )

    assert processed == [10, 20, 30]


@pytest.mark.asyncio
async def test_interface_event_uses_native_owner_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consume = AsyncMock()
    finish = AsyncMock(return_value=True)
    legacy_processor = AsyncMock()
    monkeypatch.setattr(event_worker, "session_factory", Session)
    monkeypatch.setattr(event_worker, "process_interface_update", consume)
    monkeypatch.setattr(event_worker, "finish_inbox_event", finish)
    monkeypatch.setattr(event_worker, "process_event", legacy_processor)
    event = cast(
        InboxEvent,
        SimpleNamespace(
            id=9,
            bot_id=None,
            update_id=100,
            attempts=1,
            payload={"update_id": 100, "message": {}},
        ),
    )

    await event_worker.process_claimed_event(event, "worker:native", 3)

    consume.assert_awaited_once_with(event.payload)
    finish.assert_awaited_once()
    legacy_processor.assert_not_awaited()


@pytest.mark.asyncio
async def test_claimed_batch_bulk_completes_unsupported_client_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
