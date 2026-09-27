from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gramly_welcome import view_monitor
from gramly_welcome.models import Owner, ViewMonitor
from gramly_welcome.view_reports import ViewReport


@pytest.mark.asyncio
async def test_daily_monitor_sends_once_after_fresh_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    monitor = ViewMonitor(id=1, owner_id=7, channel_id=-10042, enabled=True, last_sent_on=None)
    report = ViewReport(
        title="Канал & друзья", subscribers=1200, observed_at=datetime.now(UTC),
        points=(), views_24h=None, er24=None,
    )
    scalar_calls = 0

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def begin(self) -> Session:
            return self

        async def scalars(self, _statement: object) -> SimpleNamespace:
            nonlocal scalar_calls
            scalar_calls += 1
            return SimpleNamespace(all=lambda: [monitor] if scalar_calls == 1 else [])

        async def get(self, model: object, _key: int, **_kwargs: object) -> object:
            return SimpleNamespace(telegram_id=42) if model is Owner else monitor if model is ViewMonitor else None

    send = AsyncMock()
    monkeypatch.setattr(view_monitor, "session_factory", Session)
    monkeypatch.setattr(view_monitor, "analytics_available", AsyncMock(return_value=True))
    monkeypatch.setattr(view_monitor, "_report_for", AsyncMock(return_value=report))
    monkeypatch.setattr(view_monitor, "_send", send)
    monkeypatch.setattr(view_monitor, "required_channel_config", AsyncMock(return_value=SimpleNamespace(enabled=True, channel_id=-1001)))
    monkeypatch.setattr(view_monitor, "check_required_membership", AsyncMock(return_value=SimpleNamespace(allowed=True, temporarily_unavailable=False)))
    monkeypatch.setattr(view_monitor, "view_bot", lambda: object())
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    await view_monitor.send_daily()
    assert monitor.last_sent_on == datetime.now(view_monitor.MOSCOW).date()
    assert send.await_count == 1
    assert send.await_args is not None
    assert "нет данных" in send.await_args.args[1]
    assert "Канал &amp; друзья" in send.await_args.args[1]
