from __future__ import annotations

import asyncio
from typing import cast

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
    claimed = [cast(InboxEvent, object()) for _ in range(12)]

    await event_worker.process_claimed_batch(
        claimed,
        worker_id="worker:test",
        max_attempts=3,
        concurrency=4,
    )

    assert active == 0
    assert maximum == 4
