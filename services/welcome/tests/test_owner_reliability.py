from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery, SendMessage

from gramly_welcome.models import OwnerControlState
from gramly_welcome.owner_actions import ActionContext, TransactionalRedis, current_action, owner_actor_id
from gramly_welcome.owner_bot import _safe_callback_ack
from gramly_welcome.owner_ui import OwnerUiSession
from gramly_welcome.workers import health as health_module


@pytest.mark.asyncio
async def test_fsm_is_buffered_until_outer_action_commits() -> None:
    redis = AsyncMock()
    redis.get.return_value = b'{"draft": 1}'
    storage = TransactionalRedis(redis)
    state = OwnerControlState(actor_id=11, revision=0, storage={})
    context = ActionContext(1, state)
    token = current_action.set(context)
    try:
        assert json.loads(await storage.get("fsm:data")) == {"draft": 1}
        await storage.set("fsm:data", '{"draft":2}')
        assert json.loads(await storage.get("fsm:data")) == {"draft": 2}
        await storage.delete("fsm:data")
        assert await storage.get("fsm:data") is None
        redis.set.assert_not_awaited()
        redis.delete.assert_not_awaited()
        assert state.storage == {}
    finally:
        current_action.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["{broken", "[]", '{"actions": [], "markup": "bad", "history": 2}'])
async def test_corrupt_ui_can_render_start(broken: str) -> None:
    client: Any = SimpleNamespace(id=1, send_message=AsyncMock(return_value=SimpleNamespace(message_id=5)))
    redis = AsyncMock()
    redis.get.return_value = broken
    ui = OwnerUiSession(client, redis, 11, 11)
    await ui.load()
    await ui.render("Главное меню", None)
    assert ui.data["message_id"] == 5


@pytest.mark.asyncio
async def test_callback_ack_failure_does_not_discard_action_but_real_send_failure_propagates() -> None:
    method = AnswerCallbackQuery(callback_query_id="test")
    request = AsyncMock(side_effect=TelegramNetworkError(method=method, message="offline"))
    assert await _safe_callback_ack(request, AsyncMock(), method)
    with pytest.raises(TelegramNetworkError):
        await _safe_callback_ack(request, AsyncMock(), SendMessage(chat_id=11, text="x"))


def test_health_distinguishes_dependency_outage_from_stuck_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(health_module, "monotonic", lambda: now[0])
    state = health_module.WorkerHealth({"worker": 20})
    assert state.live() and not state.ready()
    state.mark("worker", healthy=True)
    assert state.ready()
    now[0] += 10
    state.mark("worker", healthy=False)
    assert state.live() and not state.ready()
    now[0] += 21
    assert not state.live()


def test_actor_mapping_and_ignored_update_are_stable() -> None:
    assert (
        owner_actor_id({"update_id": 2, "callback_query": {"from": {"id": 1234567890123}}}) == 1234567890123
    )
    assert owner_actor_id({"update_id": 2}) == -2
