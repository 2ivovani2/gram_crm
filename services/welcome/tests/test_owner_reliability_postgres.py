from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import EditMessageText
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome import db, owner_actions, owner_bot, owner_reply
from gramly_welcome.models import InboxEvent, InboxSource, Owner, OwnerAction, OwnerControlState, OwnerReply
from gramly_welcome.workers import owner as worker

DATABASE_URL = os.getenv("WELCOME_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="WELCOME_TEST_DATABASE_URL is not set")


@pytest.fixture
async def store(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[async_sessionmaker[db.ApplicationSession]]:
    assert DATABASE_URL
    engine = create_async_engine(DATABASE_URL, pool_size=10)
    sessions = async_sessionmaker(engine, class_=db.ApplicationSession, expire_on_commit=False)
    monkeypatch.setattr(owner_actions, "independent_session", sessions)
    monkeypatch.setattr(worker, "independent_session", sessions)
    monkeypatch.setattr(owner_reply, "engine", engine)
    yield sessions
    async with sessions() as session, session.begin():
        # This fixture owns these new tables in an explicitly configured test DB.
        await session.execute(delete(OwnerReply))
        await session.execute(delete(OwnerAction))
        await session.execute(delete(OwnerControlState))
    await engine.dispose()


def payload(update_id: int, actor: int = 333) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1700000000,
            "chat": {"id": actor, "type": "private"},
            "from": {"id": actor, "is_bot": False, "first_name": "Test"},
            "text": "/start",
        },
    }


def screen(anchor: int | None = 44) -> dict[str, Any]:
    return {
        "chat_id": 333,
        "anchor": anchor,
        "text": "Saved",
        "markup": {"inline_keyboard": []},
        "storage_key": "ui",
    }


async def seed(
    sessions: async_sessionmaker[db.ApplicationSession],
    *,
    anchor: int | None = 44,
    status: str = "pending",
    actor: int = 333,
) -> None:
    async with sessions() as session, session.begin():
        session.add(OwnerControlState(actor_id=actor, revision=1, storage={}))
        session.add(OwnerAction(update_id=actor, actor_id=actor))
        await session.flush()
        session.add(
            OwnerReply(
                update_id=actor,
                actor_id=actor,
                revision=1,
                payload=screen(anchor),
                status=status,
                lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )


@pytest.mark.asyncio
async def test_atomic_action_receipt_and_replay(
    store: async_sessionmaker[db.ApplicationSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = -(uuid.uuid4().int % 1_000_000_000)
    async with store() as session, session.begin():
        customer = Owner(telegram_id=actor, guide_step=0)
        session.add(customer)
        await session.flush()
        customer_id = customer.id
    calls = 0

    async def handler(*_: Any) -> None:
        nonlocal calls
        calls += 1
        async with db.session_factory() as nested:
            row = await nested.get(Owner, customer_id)
            assert row
            row.guide_step += 1
            await nested.commit()
        owner_actions.stage_screen(screen())

    monkeypatch.setattr(
        owner_bot, "interface_dispatcher", lambda: SimpleNamespace(feed_update=handler, storage=None)
    )
    monkeypatch.setattr(owner_bot, "interface_bot", lambda: None)
    try:
        await asyncio.gather(*(owner_actions.run_owner_action(payload(100, actor)) for _ in range(3)))
        async with store() as session:
            row = await session.get(Owner, customer_id)
            assert row and row.guide_step == 1
            assert await session.scalar(select(func.count()).select_from(OwnerAction)) == 1
            assert await session.scalar(select(func.count()).select_from(OwnerReply)) == 1
        assert calls == 1

        async def failing(*args: Any) -> None:
            await handler(*args)
            raise RuntimeError("failure after service commit")

        monkeypatch.setattr(
            owner_bot, "interface_dispatcher", lambda: SimpleNamespace(feed_update=failing, storage=None)
        )
        with pytest.raises(RuntimeError):
            await owner_actions.run_owner_action(payload(101, actor))
        async with store() as session:
            row = await session.get(Owner, customer_id)
            assert row and row.guide_step == 1
            assert await session.get(OwnerAction, 101) is None
            assert await session.get(OwnerReply, 101) is None
    finally:
        async with store() as session, session.begin():
            await session.execute(delete(Owner).where(Owner.id == customer_id))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,expected",
    [
        (TimeoutError(), "retry"),
        (TelegramRetryAfter(method=EditMessageText(text="x"), message="Retry", retry_after=17), "retry"),
        (TelegramForbiddenError(method=EditMessageText(text="x"), message="blocked"), "blocked"),
    ],
)
async def test_reply_failure_never_reenters_action(
    store: async_sessionmaker[db.ApplicationSession], failure: Exception, expected: str
) -> None:
    await seed(store)
    bot = AsyncMock()
    bot.edit_message_text.side_effect = failure
    assert await owner_reply.deliver_next_reply(bot)
    bot.send_message.assert_not_awaited()
    async with store() as session, session.begin():
        row = await session.get(OwnerReply, 333)
        assert row and row.status == expected
        if expected == "retry":
            assert row.available_at > datetime.now(UTC)
            row.available_at = datetime.now(UTC) - timedelta(seconds=1)
    if expected == "retry":
        bot.edit_message_text.side_effect = None
        assert await owner_reply.deliver_next_reply(bot)
        assert not await owner_reply.deliver_next_reply(bot)
        async with store() as session:
            row = await session.get(OwnerReply, 333)
            assert row and row.status == "delivered"


@pytest.mark.asyncio
@pytest.mark.parametrize("anchor,expected", [(None, "uncertain"), (44, "delivered")])
async def test_crashed_sending_recovery(
    store: async_sessionmaker[db.ApplicationSession], anchor: int | None, expected: str
) -> None:
    await seed(store, anchor=anchor, status="sending")
    bot = AsyncMock()
    assert await owner_reply.deliver_next_reply(bot)
    bot.send_message.assert_not_awaited()
    async with store() as session:
        row = await session.get(OwnerReply, 333)
        assert row and row.status == expected


@pytest.mark.asyncio
async def test_initial_send_timeout_is_not_blindly_retried(
    store: async_sessionmaker[db.ApplicationSession],
) -> None:
    await seed(store, anchor=None)
    bot = AsyncMock()
    bot.send_message.side_effect = TimeoutError()
    assert await owner_reply.deliver_next_reply(bot)
    assert not await owner_reply.deliver_next_reply(bot)
    bot.send_message.assert_awaited_once()
    async with store() as session:
        row = await session.get(OwnerReply, 333)
        assert row and row.status == "uncertain"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["none", "send_timeout", "delete_timeout"])
async def test_start_replacement_is_durable_before_cleanup_and_never_resends(
    store: async_sessionmaker[db.ApplicationSession], failure: str,
) -> None:
    await seed(store, anchor=None)
    async with store() as session, session.begin():
        reply = await session.get(OwnerReply, 333)
        state = await session.get(OwnerControlState, 333)
        assert reply and state
        reply.payload = {**reply.payload, "replace_anchor": 44}
        state.storage = {"ui": json.dumps({"message_id": 44})}
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=99)
    if failure == "send_timeout":
        bot.send_message.side_effect = TimeoutError()

    async def delete_after_commit(*args: Any, **kwargs: Any) -> None:
        async with store() as session:
            saved = await session.get(OwnerControlState, 333)
            receipt = await session.get(OwnerReply, 333)
            assert saved and json.loads(saved.storage["ui"])["message_id"] == 99
            assert receipt and receipt.status == "delivered"
        if failure == "delete_timeout":
            raise TimeoutError()

    bot.delete_message.side_effect = delete_after_commit
    assert await owner_reply.deliver_next_reply(bot)
    assert not await owner_reply.deliver_next_reply(bot)
    bot.send_message.assert_awaited_once()
    bot.edit_message_text.assert_not_awaited()
    async with store() as session:
        state = await session.get(OwnerControlState, 333)
        reply = await session.get(OwnerReply, 333)
        assert state and reply
        screen_state = json.loads(state.storage["ui"])
        if failure == "send_timeout":
            assert reply.status == "uncertain"
            assert screen_state["message_id"] == 44
            bot.delete_message.assert_not_awaited()
        else:
            assert reply.status == "delivered"
            assert reply.payload["delivery_method"] == "send"
            assert reply.payload["delivered_message_id"] == 99
            assert screen_state["message_id"] == 99
            assert screen_state["retired_control_ids"] == ([44] if failure == "delete_timeout" else [])
            bot.delete_message.assert_awaited_once_with(333, 44, request_timeout=3)


@pytest.mark.asyncio
async def test_sending_marker_committed_before_telegram(
    store: async_sessionmaker[db.ApplicationSession],
) -> None:
    await seed(store, anchor=None)

    async def send(*args: Any, **kwargs: Any) -> Any:
        async with store() as session:
            row = await session.get(OwnerReply, 333)
            assert row and row.status == "sending" and row.lease_expires_at
        return SimpleNamespace(message_id=99)

    bot = AsyncMock()
    bot.send_message.side_effect = send
    await owner_reply.deliver_next_reply(bot)
    async with store() as session:
        row = await session.get(OwnerControlState, 333)
        assert row and "99" in row.storage["ui"]


@pytest.mark.asyncio
async def test_busy_actor_does_not_block_other_replies(
    store: async_sessionmaker[db.ApplicationSession],
) -> None:
    await seed(store, actor=333)
    await seed(store, actor=444)
    async with store() as session, session.begin():
        await session.execute(
            text(f"SELECT pg_advisory_xact_lock({owner_actions.ACTOR_LOCK})"), {"actor": 333}
        )
        bot = AsyncMock()
        assert await owner_reply.deliver_next_reply(bot)
        bot.edit_message_text.assert_awaited_once()
        async with store() as check:
            first, second = await check.get(OwnerReply, 333), await check.get(OwnerReply, 444)
            assert first and first.status == "pending"
            assert second and second.status == "delivered"


@pytest.mark.asyncio
async def test_stale_reply_is_superseded(store: async_sessionmaker[db.ApplicationSession]) -> None:
    await seed(store)
    async with store() as session, session.begin():
        row = await session.get(OwnerControlState, 333)
        assert row
        row.revision = 2
    bot = AsyncMock()
    assert await owner_reply.deliver_next_reply(bot)
    bot.edit_message_text.assert_not_awaited()
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_serializes_actor_and_recovers_expired_lease(
    store: async_sessionmaker[db.ApplicationSession],
) -> None:
    key = f"owner-test-{uuid.uuid4().hex}"
    async with store() as session, session.begin():
        session.add(InboxSource(source_key=key, pending_count=3))
        await session.flush()
        for update_id, actor in [(1, 123), (2, 123), (3, 456)]:
            session.add(
                InboxEvent(
                    source_key=key, update_id=update_id, actor_id=actor, payload=payload(update_id, actor)
                )
            )
    try:
        claimed = await asyncio.gather(*(worker.claim_owner_event(f"worker-{i}") for i in range(3)))
        assert {event.update_id for event in claimed if event} == {1, 3}
        assert await worker.claim_owner_event("other") is None
        first = next(event for event in claimed if event and event.update_id == 1)
        async with store() as session, session.begin():
            row = await session.get(InboxEvent, first.id)
            assert row
            row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        recovered = await worker.claim_owner_event("recovered")
        assert recovered and recovered.id == first.id and recovered.attempts == 2
    finally:
        async with store() as session, session.begin():
            await session.execute(delete(InboxEvent).where(InboxEvent.source_key == key))
            await session.execute(delete(InboxSource).where(InboxSource.source_key == key))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["none", "edit_timeout", "delete_timeout"])
async def test_input_cleanup_after_durable_reply_does_not_replay_action(
    store: async_sessionmaker[db.ApplicationSession], failure: str,
) -> None:
    await seed(store)
    async with store() as session, session.begin():
        state = await session.get(OwnerControlState, 333)
        assert state
        state.storage = {"ui": json.dumps({"message_id": 44, "input_ids": [70, 71, 44]})}
    bot = AsyncMock()
    if failure == "edit_timeout":
        bot.edit_message_text.side_effect = TimeoutError()

    async def delete_after_commit(*args: Any, **kwargs: Any) -> None:
        async with store() as session:
            reply = await session.get(OwnerReply, 333)
            assert reply and reply.status == "delivered"
            assert await session.get(OwnerAction, 333) is not None
        if failure == "delete_timeout":
            raise TimeoutError()

    bot.delete_message.side_effect = delete_after_commit
    assert await owner_reply.deliver_next_reply(bot)
    async with store() as session:
        state = await session.get(OwnerControlState, 333)
        reply = await session.get(OwnerReply, 333)
        assert state and reply
        pending = json.loads(state.storage["ui"])["input_ids"]
        if failure == "edit_timeout":
            assert reply.status == "retry"
            assert pending == [70, 71, 44]
            bot.delete_message.assert_not_awaited()
        else:
            assert reply.status == "delivered"
            assert pending == ([70, 71] if failure == "delete_timeout" else [])
            assert [call.args[1] for call in bot.delete_message.call_args_list] == [70, 71]
    if failure != "edit_timeout":
        assert not await owner_reply.deliver_next_reply(bot)
        bot.edit_message_text.assert_awaited_once()
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_intent_commits_with_fsm_and_never_deletes_on_rollback(
    store: async_sessionmaker[db.ApplicationSession], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aiogram.types import Update

    from gramly_welcome.owner_ui import OwnerUiMiddleware, render_owner_ui

    client = AsyncMock()
    client.id = 100
    redis = owner_actions.TransactionalRedis(SimpleNamespace(get=AsyncMock(return_value=None)))
    state = SimpleNamespace(storage=SimpleNamespace(redis=redis))
    fail = False

    async def handler(event: Any, data: Any) -> None:
        await render_owner_ui(event, "Сохранено")
        await redis.set("fsm", "saved")

    async def feed(_bot: Any, update: Update) -> None:
        assert update.message is not None
        await OwnerUiMiddleware()(handler, update.message, {"bot": client, "state": state})
        client.delete_message.assert_not_awaited()
        if fail:
            raise RuntimeError("database action interrupted")

    monkeypatch.setattr(owner_bot, "interface_bot", lambda: client)
    monkeypatch.setattr(owner_bot, "interface_dispatcher", lambda: SimpleNamespace(feed_update=feed, storage=None))
    assert await owner_actions.run_owner_action(payload(70)) == "applied"
    async with store() as session:
        saved = await session.get(OwnerControlState, 333)
        assert saved and saved.storage["fsm"] == "saved"
        key = "welcome:owner-ui:100:333:333"
        assert json.loads(saved.storage[key])["input_ids"] == [70]
    # A separate actor avoids cleaning the previous actor's committed input.
    fail = True
    with pytest.raises(RuntimeError):
        await owner_actions.run_owner_action(payload(71, 444))
    async with store() as session:
        assert await session.get(OwnerControlState, 444) is None
        assert await session.get(OwnerReply, 71) is None
    client.delete_message.assert_not_awaited()
