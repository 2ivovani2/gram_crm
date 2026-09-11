from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramUnauthorizedError
from aiogram.methods import SendMessage

from gramly_welcome import owner_bot
from gramly_welcome.telegram_errors import normalize_delivery_error
from gramly_welcome.workers.leased import run_leased


def test_failure_reasons_do_not_invent_external_approval() -> None:
    error = TelegramForbiddenError(method=SendMessage(chat_id=1, text="x"), message="Forbidden")
    assert normalize_delivery_error(error) == "write_access_unavailable"
    assert normalize_delivery_error(error, temporary=True) == "temporary_address_unavailable"
    assert normalize_delivery_error(error, temporary=True, request_closed=True) == "join_request_already_processed"
    assert normalize_delivery_error(error, temporary=True, expired=True) == "join_request_window_expired"
    assert normalize_delivery_error(TimeoutError()) == "telegram_temporary_error"
    assert normalize_delivery_error(TelegramUnauthorizedError(
        method=SendMessage(chat_id=1, text="x"), message="secret provider text",
    )) == "invalid_bot_token"


@pytest.mark.asyncio
async def test_leased_work_propagates_failure_and_cancels_on_shutdown() -> None:
    work = AsyncMock(side_effect=RuntimeError("test"))
    with pytest.raises(RuntimeError, match="test"):
        await run_leased(work(), object, 1, "test-worker")
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def blocking() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    task = asyncio.create_task(run_leased(blocking(), object, 1, "test-worker"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [False, True])
async def test_replacement_cannot_change_another_owner_or_another_bot(
    monkeypatch: pytest.MonkeyPatch, owned: bool,
) -> None:
    message, state = AsyncMock(), AsyncMock()
    message.text = "test-token"
    state.get_data.return_value = {"bot_id": 12}
    owner: Any = SimpleNamespace(id=1)
    managed: Any = SimpleNamespace(id=12, telegram_id=100)
    monkeypatch.setattr(owner_bot, "_get_owned", AsyncMock(return_value=managed if owned else None))
    answer, webhook = AsyncMock(), AsyncMock()
    monkeypatch.setattr(owner_bot, "owner_answer", answer)
    monkeypatch.setattr(owner_bot, "configure_customer_webhook", webhook)
    monkeypatch.setattr(owner_bot, "get_settings", lambda: SimpleNamespace())
    probe = AsyncMock()
    probe.__aenter__.return_value = probe
    probe.get_me.return_value = SimpleNamespace(id=200)
    monkeypatch.setattr(owner_bot, "Bot", lambda *args, **kwargs: probe)
    await owner_bot.receive_replacement_token(message, owner, state)
    webhook.assert_not_awaited()
    if owned:
        state.clear.assert_not_awaited()
        assert "другого бота" in answer.call_args.args[1]
    else:
        probe.get_me.assert_not_awaited()
        state.clear.assert_awaited_once()
