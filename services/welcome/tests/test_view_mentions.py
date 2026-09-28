from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import utils

from gramly_welcome import view_mentions
from gramly_welcome.models import ViewMentionMonitor
from gramly_welcome.view_mentions import mentioned_usernames, record_mentions


def test_mentions_include_hidden_links_and_ignore_similar_handles() -> None:
    names = mentioned_usernames(
        "@ChannelOne https://t.me/channelTwo/42 @channelOnex",
        ["https://t.me/thirdChan/9", "https://example.com/channelFour"],
    )
    assert names == {"channelone", "channeltwo", "channelonex", "thirdchan"}


@pytest.mark.asyncio
async def test_mentions_are_opt_in_new_posts_only_and_never_write_private_sources() -> None:
    now = datetime.now(UTC)
    monitor = SimpleNamespace(id=7, channel_id=123, username="targetchan", created_at=now)

    class Session:
        statements: list[object] = []

        async def execute(self, statement: object) -> None:
            self.statements.append(statement)

    session = Session()
    rows = [
        (10, now - timedelta(minutes=1), "@targetchan", []),
        (11, now + timedelta(seconds=1), "not visible", ["https://t.me/TargetChan/5"]),
        (12, now + timedelta(seconds=1), "@different", []),
    ]
    assert await record_mentions(cast(AsyncSession, session), [cast(ViewMentionMonitor, monitor)], source_channel_id=456, source_username="", posts=rows) == 0
    assert not session.statements
    assert await record_mentions(
        cast(AsyncSession, session), [cast(ViewMentionMonitor, monitor)], source_channel_id=456, source_username="publicsource", posts=rows,
    ) == 1
    assert len(session.statements) == 1
    params = cast(Any, session.statements[0]).compile().params
    assert 11 in params.values()
    assert 10 not in params.values()


@pytest.mark.asyncio
async def test_subscription_cap_cannot_create_hidden_active_monitors(monkeypatch: pytest.MonkeyPatch) -> None:
    class Session:
        scalars = iter((None, 20))

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def begin(self) -> Session:
            return self

        async def get(self, *_args: object, **_kwargs: object) -> object:
            return object()

        async def scalar(self, _statement: object) -> object:
            return next(self.scalars)

        async def execute(self, _statement: object) -> None:
            raise AssertionError("subscription must not be inserted past the cap")

    monkeypatch.setattr(view_mentions, "session_factory", Session)
    with pytest.raises(ValueError, match="не больше 20"):
        await view_mentions.subscribe_mentions(1, -1001, "publicchan")


@pytest.mark.asyncio
async def test_dispatch_drains_multiple_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = AsyncMock(side_effect=[(20, 20), (3, 3)])
    monkeypatch.setattr(view_mentions, "_dispatch_batch", batch)
    assert await view_mentions.dispatch_pending(limit=20, time_budget_seconds=60) == 23
    assert batch.await_count == 2


@pytest.mark.asyncio
async def test_disabled_membership_gate_sends_without_telegram_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    from gramly_welcome import required_channel, view_bot
    from gramly_welcome.models import Owner, ViewMentionEvent, ViewMentionMonitor

    event = SimpleNamespace(
        id=5, monitor_id=7, source_username="sourcechan", post_id=12,
        claim_until=None, notified_at=None, attempts=0,
    )
    monitor = SimpleNamespace(id=7, owner_id=9, username="targetchan", enabled=True)
    owner = SimpleNamespace(id=9, telegram_id=12345)

    class Session:
        active = 0

        async def __aenter__(self) -> Session:
            Session.active += 1
            return self

        async def __aexit__(self, *_args: object) -> None:
            Session.active -= 1

        def begin(self) -> Session:
            return self

        async def scalars(self, _statement: object) -> object:
            return SimpleNamespace(all=lambda: [event])

        async def get(self, model: object, _id: int, **_kwargs: object) -> object:
            return cast(dict[object, object], {ViewMentionEvent: event, ViewMentionMonitor: monitor, Owner: owner})[model]

    async def send(*_args: object, **_kwargs: object) -> None:
        assert Session.active == 0  # Telegram I/O must not hold a DB connection.

    lookup = AsyncMock(side_effect=AssertionError("disabled gate must not query membership"))
    monkeypatch.setattr(view_mentions, "session_factory", Session)
    monkeypatch.setattr(required_channel, "required_channel_config", AsyncMock(
        return_value=SimpleNamespace(enabled=False, channel_id=0),
    ))
    monkeypatch.setattr(view_bot, "view_bot", lambda: SimpleNamespace(get_chat_member=lookup))
    monkeypatch.setattr(view_bot, "_send", send)
    assert await view_mentions._dispatch_batch(20, {}) == (1, 1)
    assert event.notified_at is not None
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_collector_flushes_alerts_after_failed_channel_before_next_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gramly_welcome import view_collector

    events: list[str] = []
    query_count = 0

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def begin(self) -> Session:
            return self

        async def execute(self, _statement: object) -> object:
            nonlocal query_count
            query_count += 1
            if query_count == 1:
                return SimpleNamespace(all=lambda: [(101, "firstchan", None), (102, "secondchan", None)])
            if query_count == 2:
                return SimpleNamespace(all=lambda: [])
            return SimpleNamespace(rowcount=0)

        def add(self, _row: object) -> None:
            return None

    class Client:
        async def get_entity(self, name: object) -> object:
            events.append(f"scan:{name}")
            if name == "firstchan":
                raise RuntimeError("one channel unavailable")
            return SimpleNamespace(peer_id=102, broadcast=True, username="secondchan")

        async def __call__(self, _request: object) -> object:
            return SimpleNamespace(full_chat=SimpleNamespace(participants_count=None))

    @asynccontextmanager
    async def lease(*_args: object, **_kwargs: object):  # type: ignore[no-untyped-def]
        yield 1, Client()

    async def dispatch(**_kwargs: object) -> int:
        events.append("dispatch")
        return 0

    monkeypatch.setattr(view_collector, "analytics_available", AsyncMock(return_value=True))
    monkeypatch.setattr(view_collector, "session_factory", Session)
    monkeypatch.setattr(view_collector, "lease_account", lease)
    collect_one = AsyncMock(return_value=True)
    monkeypatch.setattr(view_collector, "collect_one", collect_one)
    monkeypatch.setattr(utils, "get_peer_id", lambda entity: entity.peer_id)
    monkeypatch.setattr(view_collector, "dispatch_pending", dispatch)
    healthy = {name: (0, 0, 0.0) for name in (
        "deliveries", "approvals", "join_activations", "content_operations",
    )}
    queue = AsyncMock(return_value=healthy)
    monkeypatch.setattr(view_collector, "queue_snapshot", queue)
    await view_collector.collect()
    assert events[:3] == ["scan:firstchan", "dispatch", "scan:secondchan"]

    query_count = 0
    events.clear()
    queue.return_value = {**healthy, "deliveries": (1, 0, 6.0)}
    previous_scans = collect_one.await_count
    await view_collector.collect()
    assert not any(event.startswith("scan:") for event in events)
    assert collect_one.await_count == previous_scans
