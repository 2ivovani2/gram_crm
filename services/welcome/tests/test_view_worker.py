from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from types import SimpleNamespace
from typing import cast

import pytest

from gramly_welcome.models import InboxEvent
from gramly_welcome.workers import view


@pytest.mark.asyncio
async def test_view_batch_orders_each_person_without_blocking_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline: list[str] = []

    async def process(event: InboxEvent, _worker_id: str, _attempts: int) -> None:
        timeline.append(f"start:{event.update_id}")
        if event.update_id == 10:
            await asyncio.sleep(0.02)
        timeline.append(f"finish:{event.update_id}")

    async def leased(work: object, *_args: object, **_kwargs: object) -> None:
        await cast(Awaitable[None], work)

    monkeypatch.setattr(view, "process_claimed_event", process)
    monkeypatch.setattr(view, "run_leased", leased)
    events = [
        cast(InboxEvent, SimpleNamespace(id=1, actor_id=1, update_id=20)),
        cast(InboxEvent, SimpleNamespace(id=2, actor_id=2, update_id=30)),
        cast(InboxEvent, SimpleNamespace(id=3, actor_id=1, update_id=10)),
    ]

    await view.process_view_batch(events, "view:test", 3)

    assert timeline.index("start:10") < timeline.index("finish:10") < timeline.index("start:20")
    assert timeline.index("start:30") < timeline.index("finish:10")
