from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from gramly_welcome.metrics import (
    VIEW_COLLECTION_FAILURES,
    VIEW_LAST_SUCCESS_AGE,
    VIEW_QUEUE_AGE,
    VIEW_QUEUE_DEPTH,
)
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


@pytest.mark.asyncio
async def test_view_metrics_separate_pending_work_from_collection_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)

    class Session:
        calls = 0

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def execute(self, _query: object) -> SimpleNamespace:
            self.calls += 1
            row = (2, now - timedelta(seconds=30)) if self.calls == 1 else (1, now - timedelta(seconds=40))
            return SimpleNamespace(
                one_or_none=lambda: row,
                one=lambda: row,
            )

        async def scalar(self, _query: object) -> int | datetime:
            self.calls += 1
            return 3 if self.calls == 3 else now - timedelta(minutes=4)

    monkeypatch.setattr(view, "session_factory", Session)
    await view.refresh_view_metrics()

    assert VIEW_QUEUE_DEPTH._value.get() == 3
    assert 40 <= VIEW_QUEUE_AGE._value.get() < 42
    assert VIEW_COLLECTION_FAILURES._value.get() == 3
    assert 240 <= VIEW_LAST_SUCCESS_AGE._value.get() < 242
