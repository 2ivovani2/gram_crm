from __future__ import annotations

import asyncio
import os
import uuid
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
    ContentAttachment,
    ContentFlow,
    ContentFlowVersion,
    ContentKeyboard,
    ContentKeyboardButton,
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


def test_catalog_accepts_multiple_media_and_premium_entities_in_one_step() -> None:
    data = deepcopy(starter_entries()[0])
    data["spec"]["steps"][0] = {
        "payload": {"type": "text", "text": "Hi ⭐", "entities": [
            {"type": "custom_emoji", "offset": 3, "length": 1, "custom_emoji_id": "123456789"}]},
        "attachments": [
            {"media_type": "photo", "storage_key": "catalog/uploads/one.jpg", "original_name": "one.jpg"},
            {"media_type": "document", "storage_key": "catalog/uploads/two.docx", "original_name": "two.docx"}],
        "keyboard": {"kind": "inline", "rows": [[{"text": "Сайт", "action_type": "url",
            "value": "https://gramly.tech", "style": "primary"}]]},
    }
    entry = CatalogEntry.model_validate(data)
    assert len(entry.spec.steps[0].attachments) == 2
    assert entry.spec.steps[0].payload["entities"][0]["custom_emoji_id"] == "123456789"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["catalog:5:welcome:0", "catalog-filter:5:welcome", "catalog-view:5:6:0", "catalog-add:5:6"])
async def test_old_catalog_buttons_open_mini_app_without_install(monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    from gramly_welcome import owner_bot

    install = AsyncMock()
    answer = AsyncMock()
    monkeypatch.setattr(owner_bot, "install_template", install)
    monkeypatch.setattr(owner_bot, "owner_answer", answer)
    monkeypatch.setattr(owner_bot, "_get_owned", AsyncMock(return_value=SimpleNamespace(id=5)))
    callback = SimpleNamespace(data=action, message=Mock(), answer=AsyncMock())
    await owner_bot.catalog_menu(callback, SimpleNamespace(id=7))
    install.assert_not_awaited()
    markup = answer.call_args.kwargs["reply_markup"]
    button = markup.inline_keyboard[0][0]
    assert button.callback_data is None
    assert button.web_app and "section=catalog" in button.web_app.url
    assert "bot_id=5" in button.web_app.url
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_old_catalog_button_checks_bot_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    from gramly_welcome import owner_bot

    answer = AsyncMock()
    monkeypatch.setattr(owner_bot, "owner_answer", answer)
    monkeypatch.setattr(owner_bot, "_get_owned", AsyncMock(return_value=None))
    callback = SimpleNamespace(data="catalog-add:5:6", message=Mock(), answer=AsyncMock())
    await owner_bot.catalog_menu(callback, SimpleNamespace(id=7))
    answer.assert_not_awaited()
    callback.answer.assert_awaited_once_with("Бот недоступен", show_alert=True)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WELCOME_TEST_DATABASE_URL"), reason="PostgreSQL required")
async def test_catalog_install_is_atomic_independent_unassigned_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    from gramly_welcome import message_catalog
    engine = create_async_engine(os.environ["WELCOME_TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(message_catalog, "session_factory", sessions)
    uid = uuid.uuid4().hex[:12]
    source = deepcopy(starter_entries()[0])
    source["spec"]["steps"][0] = {
        "payload": {"type": "text", "text": "Hi ⭐", "entities": [
            {"type": "custom_emoji", "offset": 3, "length": 1, "custom_emoji_id": "123456789"}]},
        "attachments": [
            {"media_type": "photo", "storage_key": "catalog/uploads/one.jpg", "original_name": "one.jpg"},
            {"media_type": "document", "storage_key": "catalog/uploads/two.docx", "original_name": "two.docx"}],
        "keyboard": {"kind": "inline", "rows": [[{"text": "Сайт", "action_type": "url",
            "value": "https://gramly.tech", "style": "primary"}]]},
    }
    entry = CatalogEntry.model_validate({**source, "slug": f"test-{uid}"})
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
            assert step.payload["entities"][0]["custom_emoji_id"] == "123456789"
            attachments = (await session.scalars(select(ContentAttachment).where(ContentAttachment.step_id == step.id).order_by(ContentAttachment.position))).all()
            assert [a.storage_key for a in attachments] == ["catalog/uploads/one.jpg", "catalog/uploads/two.docx"]
            keyboard = await session.scalar(select(ContentKeyboard).where(ContentKeyboard.step_id == step.id))
            assert keyboard is not None
            buttons = (await session.scalars(select(ContentKeyboardButton).where(ContentKeyboardButton.keyboard_id == keyboard.id))).all()
            assert buttons[0].style == "primary"
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
