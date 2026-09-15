from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome.catalog_starters import starter_entries
from gramly_welcome.message_catalog import CatalogEntry, install_template, load_catalog
from gramly_welcome.models import (
    ContentFlow,
    ContentFlowVersion,
    ContentStep,
    FlowChannelAssignment,
    IdempotencyRecord,
    ManagedBot,
    MessageTemplate,
    Owner,
)
from gramly_welcome.storage import ObjectStorage


def test_ten_original_starters_are_valid_and_have_no_invented_conversion() -> None:
    entries = [CatalogEntry.model_validate(item) for item in starter_entries()]
    assert len(entries) == 10
    assert len({item.slug for item in entries}) == 10
    assert sum(item.kind == "welcome" for item in entries) == 5
    assert all(item.conversion is None and not item.requires_business for item in entries)
    assert all(step.keyboard is None for item in entries for step in item.spec.steps)


@pytest.mark.parametrize("change", [
    {"spec": {"steps": [{"payload": {"text": "test"}}] * 6}},
    {"spec": {"steps": [{"payload": {"text": "test"}, "delay_after_seconds": 180 * 86400 + 1}]}},
    {"screenshots": ["https://tracker.example/a.png"]},
    {"spec": {"steps": [{"payload": {"text": "test"}, "keyboard": {"kind": "reply", "rows": [[{"text": "x", "action_type": "text", "value": "x"}]]}}]}},
])
def test_invalid_catalog_is_rejected_before_loading(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CatalogEntry.model_validate({**starter_entries()[0], **change})


def test_removing_copied_media_does_not_remove_shared_catalog_asset() -> None:
    client = Mock()
    client.delete_objects.return_value = {}
    storage = SimpleNamespace(client=client, bucket="test")
    ObjectStorage._delete_many(cast(ObjectStorage, storage), ["catalog/revision-1/photo.jpg", "owners/1/photo.jpg"])
    assert client.delete_objects.call_args.kwargs["Delete"]["Objects"] == [{"Key": "owners/1/photo.jpg"}]


@pytest.mark.asyncio
async def test_bot_install_clears_old_composer_only_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from aiogram.types import Chat, Message

    from gramly_welcome import owner_bot

    @asynccontextmanager
    async def factory() -> AsyncIterator[object]:
        yield object()

    install = AsyncMock(return_value={"version_id": 23})
    editor = AsyncMock()
    monkeypatch.setattr(owner_bot, "session_factory", factory)
    monkeypatch.setattr(owner_bot, "install_template", install)
    monkeypatch.setattr(owner_bot, "send_chain_editor", editor)
    message = Message(message_id=99, date=datetime.now(UTC), chat=Chat(id=11, type="private"))
    callback = SimpleNamespace(id="test-update", data="catalog-add:5:6", message=message, answer=AsyncMock())
    owner = SimpleNamespace(id=7)
    state = AsyncMock()
    await owner_bot.catalog_add(callback, owner, state)
    state.clear.assert_awaited_once()
    editor.assert_awaited_once_with(message, owner, 23)
    assert install.call_args.kwargs["key"] == "catalog-callback:test-update"
    state.reset_mock()
    editor.reset_mock()
    install.side_effect = ValueError("Бот недоступен")
    await owner_bot.catalog_add(callback, owner, state)
    state.clear.assert_not_awaited()
    editor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WELCOME_TEST_DATABASE_URL"), reason="PostgreSQL required")
async def test_catalog_install_is_atomic_independent_unassigned_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    from gramly_welcome import message_catalog
    engine = create_async_engine(os.environ["WELCOME_TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(message_catalog, "session_factory", sessions)
    uid = uuid.uuid4().hex[:12]
    entry = CatalogEntry.model_validate({**starter_entries()[0], "slug": f"test-{uid}"})
    await load_catalog([entry], apply=False)
    async with sessions() as session:
        assert await session.scalar(select(MessageTemplate).where(MessageTemplate.slug == entry.slug)) is None
    await load_catalog([entry], apply=True)
    await load_catalog([entry], apply=True)
    owner_id = None
    try:
        async with sessions() as session:
            template = await session.scalar(select(MessageTemplate).where(MessageTemplate.slug == entry.slug))
            assert template is not None
            template_id = template.id
            owner = Owner(telegram_id=-(uuid.uuid4().int % 1_000_000_000))
            session.add(owner)
            await session.flush()
            owner_id = owner.id
            bot = ManagedBot(owner_id=owner.id, telegram_id=-(uuid.uuid4().int % 1_000_000_000),
                public_id=uuid.uuid4(), display_name="Test catalog", token_ciphertext="not-a-token", webhook_secret="test", path_secret="test")
            session.add(bot)
            await session.flush()
            bot_id = bot.id
            existing = ContentFlow(bot_id=bot_id, kind="welcome", name="Existing", assignment_mode="all")
            session.add(existing)
            await session.commit()
        async def install(key: str, owner: int = owner.id) -> dict[str, Any]:
            async with sessions() as session:
                return await install_template(session, owner_id=owner, bot_id=bot_id, template_id=template_id, key=key)
        first, replay = await asyncio.gather(install("repeat"), install("repeat"))
        assert first == replay
        second = await install("independent")
        assert second["flow_id"] != first["flow_id"]
        with pytest.raises(ValueError, match="Бот недоступен"):
            await install("foreign", owner=owner_id + 1_000_000)
        async with sessions() as session:
            assert await session.scalar(select(func.count(ContentFlow.id)).where(ContentFlow.bot_id == bot_id)) == 3
            assert await session.scalar(select(func.count(FlowChannelAssignment.id)).where(FlowChannelAssignment.flow_id == first["flow_id"])) == 0
            version = await session.get(ContentFlowVersion, first["version_id"])
            assert version is not None
            assert version.status == "draft"
            flow = await session.get(ContentFlow, first["flow_id"])
            assert flow is not None
            assert flow.assignment_mode == "selected"
            step = await session.scalar(select(ContentStep).where(ContentStep.version_id == first["version_id"]).order_by(ContentStep.position))
            assert step is not None
            step.payload = {"text": "Edited copy", "type": "text"}
            await session.commit()
        async with sessions() as session:
            step = await session.scalar(select(ContentStep).where(ContentStep.version_id == second["version_id"]).order_by(ContentStep.position))
            assert step is not None
            assert step.payload["text"] == entry.spec.steps[0].payload["text"]
        changed = deepcopy(entry)
        changed.title = "Changed immutable revision"
        with pytest.raises(ValueError, match="Immutable revision"):
            await load_catalog([changed], apply=True)
    finally:
        async with sessions() as session, session.begin():
            if owner_id:
                bots = select(ManagedBot.id).where(ManagedBot.owner_id == owner_id)
                await session.execute(delete(ContentFlow).where(ContentFlow.bot_id.in_(bots)))
                await session.execute(delete(IdempotencyRecord).where(IdempotencyRecord.owner_id == owner_id))
                await session.execute(delete(ManagedBot).where(ManagedBot.owner_id == owner_id))
                await session.execute(delete(Owner).where(Owner.id == owner_id))
            await session.execute(delete(MessageTemplate).where(MessageTemplate.slug == entry.slug))
        await engine.dispose()
