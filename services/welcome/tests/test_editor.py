from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome.config import Settings
from gramly_welcome.content_service import publish_draft
from gramly_welcome.db import ApplicationSession
from gramly_welcome.editor_api import LaunchDraft, SaveDraft, launch_editor, read_editor, save_editor
from gramly_welcome.editor_assets import emoji_set_name, owner_asset, owner_attachment
from gramly_welcome.main import app
from gramly_welcome.models import (
    Channel,
    ContentAttachment,
    ContentFlow,
    ContentFlowVersion,
    ContentKeyboard,
    ContentKeyboardButton,
    ContentStep,
    EditorMedia,
    ManagedBot,
    Owner,
)
from gramly_welcome.rich_text import validate_rich_text

PG = pytest.mark.skipif(not os.getenv("WELCOME_TEST_DATABASE_URL"), reason="PostgreSQL required")


@asynccontextmanager
async def fixture() -> AsyncIterator[Any]:
    engine = create_async_engine(os.environ["WELCOME_TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, class_=ApplicationSession, expire_on_commit=False)
    owners = []
    try:
        async with sessions() as session:
            for _ in range(2):
                owner = Owner(telegram_id=-(uuid.uuid4().int % 1_000_000_000))
                session.add(owner)
                await session.flush()
                owners.append(owner)
            bot = ManagedBot(
                owner_id=owners[0].id,
                telegram_id=-(uuid.uuid4().int % 1_000_000_000),
                public_id=uuid.uuid4(),
                display_name="Test editor",
                username="editor_test",
                token_ciphertext="not-a-token",
                webhook_secret="test",
                path_secret="test",
            )
            session.add(bot)
            await session.flush()
            flow = ContentFlow(bot_id=bot.id, kind="welcome", name="Test", assignment_mode="selected")
            channel = Channel(bot_id=bot.id, telegram_id=-1234, title="Test channel", can_invite_users=True)
            session.add_all([flow, channel])
            await session.flush()
            version = ContentFlowVersion(
                flow_id=flow.id, version=1, status="draft", author_telegram_id=owners[0].telegram_id
            )
            session.add(version)
            await session.flush()
            step = ContentStep(
                version_id=version.id,
                position=0,
                payload={
                    "type": "text",
                    "text": "👋 {name} ⭐",
                    "entities": [
                        {"type": "bold", "offset": 3, "length": 6},
                        {"type": "custom_emoji", "offset": 10, "length": 1, "custom_emoji_id": "12345"},
                    ],
                },
            )
            session.add(step)
            await session.commit()
            ids = SimpleNamespace(
                flow=flow.id, version=version.id, step=step.id, channel=channel.id, owner=owners[0].id
            )
        yield (
            sessions,
            SimpleNamespace(auth=SimpleNamespace(owner=owners[0])),
            SimpleNamespace(auth=SimpleNamespace(owner=owners[1])),
            ids,
        )
    finally:
        async with sessions() as session:
            await session.execute(delete(Owner).where(Owner.id.in_([o.id for o in owners])))
            await session.commit()
        await engine.dispose()


def body(doc: dict[str, Any]) -> SaveDraft:
    return SaveDraft(
        expected_revision=doc["version"]["edit_revision"],
        first_delay_seconds=doc["version"]["first_delay_seconds"],
        button_style=doc["version"]["button_style"],
        steps=doc["steps"],
    )


def test_editor_endpoints_require_auth() -> None:
    with TestClient(app) as client:
        for path in [
            "/api/v1/editor/flows/1",
            "/api/v1/editor/media/" + str(uuid.uuid4()),
            "/api/v1/editor/attachments/1",
            "/api/v1/editor/emoji-sets?link=https://t.me/addemoji/Set",
            "/api/v1/editor/emoji/123",
        ]:
            assert client.get(path).status_code == 401
        assert client.get("/api/admin/v1/editor/emoji-sets?link=https://t.me/addemoji/Set").status_code == 403


@pytest.mark.parametrize(
    "link",
    [
        "http://t.me/addemoji/Test",
        "https://evil.test/addemoji/Test",
        "https://t.me@evil.test/addemoji/Test",
        "https://t.me/addstickers/Test",
        "https://t.me/addemoji/../Test",
        "https://t.me/addemoji/Test?url=http://localhost",
        "https://t.me:443/addemoji/Test",
    ],
)
def test_emoji_link_rejects_arbitrary_urls(link: str) -> None:
    with pytest.raises(ValueError):
        emoji_set_name(link)


def test_entity_offsets_use_utf16_and_preserve_fallback() -> None:
    assert emoji_set_name(" https://t.me/addemoji/Hello_Set ") == "Hello_Set"
    validate_rich_text(
        {
            "text": "👋 ⭐",
            "entities": [{"type": "custom_emoji", "offset": 3, "length": 1, "custom_emoji_id": "123"}],
        }
    )
    for entity in [
        {"type": "bold", "offset": 1, "length": 1},
        {"type": "bold", "offset": 0, "length": 5},
        {"type": "custom_emoji", "offset": 3, "length": 1, "custom_emoji_id": "bad"},
    ]:
        with pytest.raises(ValueError):
            validate_rich_text({"text": "👋 ⭐", "entities": [entity]})


@PG
@pytest.mark.asyncio
async def test_atomic_save_round_trip_media_entities_keyboard_and_replay() -> None:
    async with fixture() as (sessions, user, other, ids):
        media_id = uuid.uuid4()
        async with sessions() as session:
            session.add(
                EditorMedia(
                    id=media_id,
                    owner_id=ids.owner,
                    descriptor={
                        "media_type": "photo",
                        "storage_key": "editor/private/test.png",
                        "mime_type": "image/png",
                        "original_name": "test.png",
                        "size": 100,
                        "payload": {},
                    },
                )
            )
            await session.commit()
            doc = await read_editor(ids.flow, user, session)
        data = body(doc)
        data.steps[0].attachments = [{"media_id": str(media_id), "storage_key": "forged"}]
        data.steps[0].keyboard = {
            "kind": "inline",
            "rows": [
                [{"text": "Сайт", "action_type": "url", "value": "https://gramly.tech", "style": "success"}]
            ],
        }

        async def save() -> dict[str, Any]:
            async with sessions() as session:
                return await save_editor(ids.version, data, user, session, "save")

        first, replay = await asyncio.gather(save(), save())
        assert first == replay
        step = first["steps"][0]
        assert step["payload"] == doc["steps"][0]["payload"]
        assert step["attachments"][0]["storage_key"] == "editor/private/test.png"
        assert step["attachments"][0]["payload"]["caption_entities"][-1]["custom_emoji_id"] == "12345"
        assert step["keyboard"]["rows"][0][0]["style"] == "success"
        assert first["version"]["edit_revision"] > doc["version"]["edit_revision"]
        async with sessions() as session:
            assert (await read_editor(ids.flow, user, session)) == first
            # Editing the caption changes the actual compiled media caption.
            changed = body(first)
            changed.steps[0].attachments[0]["payload"]["caption"] = "Новая подпись ⭐"
            changed.steps[0].attachments[0]["payload"]["caption_entities"] = [
                {"type": "custom_emoji", "offset": 14, "length": 1, "custom_emoji_id": "12345"}
            ]
            # Python/UTF16 count for the BMP-only caption.
            changed.steps[0].attachments[0]["payload"]["caption_entities"][0]["offset"] = 13
            second = await save_editor(ids.version, changed, user, session, "caption")
            assert second["steps"][0]["attachments"][0]["payload"]["caption"] == "Новая подпись ⭐"
        async with sessions() as session:
            with pytest.raises(HTTPException) as foreign:
                await owner_asset(media_id, other, session, Settings())
            assert foreign.value.status_code == 404
            with pytest.raises(HTTPException) as foreign_attachment:
                await owner_attachment(second["steps"][0]["attachments"][0]["id"], other, session, Settings())
            assert foreign_attachment.value.status_code == 404


@PG
@pytest.mark.asyncio
async def test_foreign_media_and_arbitrary_keys_cannot_be_saved() -> None:
    async with fixture() as (sessions, user, other, ids):
        media_id = uuid.uuid4()
        async with sessions() as session:
            session.add(EditorMedia(id=media_id, owner_id=other.auth.owner.id, descriptor={}))
            await session.commit()
            doc = await read_editor(ids.flow, user, session)
        for media in [{"media_id": str(media_id)}, {"storage_key": "catalog/foreign"}]:
            data = body(doc)
            data.steps[0].attachments = [media]
            async with sessions() as session:
                with pytest.raises(HTTPException) as rejected:
                    await save_editor(ids.version, data, user, session, str(uuid.uuid4()))
                assert rejected.value.status_code == 422
        async with sessions() as session:
            assert (await read_editor(ids.flow, user, session)) == doc


@PG
@pytest.mark.asyncio
async def test_bot_mutations_invalidate_revision_and_parallel_edit_has_one_winner() -> None:
    async with fixture() as (sessions, user, other, ids):
        async with sessions() as session:
            doc = await read_editor(ids.flow, user, session)
        async with sessions() as session:
            await session.execute(
                update(ContentStep).where(ContentStep.id == ids.step).values(delay_after_seconds=5)
            )
            await session.commit()
        async with sessions() as session:
            with pytest.raises(HTTPException) as stale:
                await save_editor(ids.version, body(doc), user, session, "stale")
            assert stale.value.status_code == 409
            doc = await read_editor(ids.flow, user, session)

        async def save(key: str) -> Any:
            async with sessions() as session:
                try:
                    return await save_editor(ids.version, body(doc), user, session, key)
                except HTTPException as exc:
                    return exc.status_code

        results = await asyncio.gather(save("tab-one"), save("tab-two"))
        assert sum(isinstance(r, dict) for r in results) == 1
        assert 409 in results
        async with sessions() as session:
            fresh = await read_editor(ids.flow, user, session)
            step_id = fresh["steps"][0]["id"]
            old = fresh["version"]["edit_revision"]
            media = ContentAttachment(
                step_id=step_id, position=0, media_type="photo", storage_key="catalog/a.png", payload={}
            )
            keyboard = ContentKeyboard(step_id=step_id, kind="inline", settings={})
            session.add_all([media, keyboard])
            await session.flush()
            button = ContentKeyboardButton(
                keyboard_id=keyboard.id,
                row=0,
                position=0,
                text="Site",
                action_type="url",
                value="https://gramly.tech",
                style="default",
            )
            session.add(button)
            await session.commit()
            fresh = await read_editor(ids.flow, user, session)
            assert fresh["version"]["edit_revision"] >= old + 3
            old = fresh["version"]["edit_revision"]
            button.text = "Changed"
            media.original_name = "Changed"
            await session.commit()
            assert (await read_editor(ids.flow, user, session))["version"]["edit_revision"] >= old + 2


@PG
@pytest.mark.asyncio
async def test_launch_rolls_back_assignment_failure_and_publication_failure_then_replays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gramly_welcome import editor_api
    from gramly_welcome.models import FlowChannelAssignment

    async with fixture() as (sessions, user, other, ids):
        async with sessions() as session:
            doc = await read_editor(ids.flow, user, session)
        expected = doc["version"]["edit_revision"]
        async with sessions() as session:
            with pytest.raises(HTTPException):
                await launch_editor(
                    ids.version,
                    LaunchDraft(expected_revision=expected, channel_ids=[ids.channel + 99999]),
                    user,
                    session,
                    "bad-channel",
                )
        original = publish_draft

        async def fail(*args: Any) -> Any:
            raise ValueError("Synthetic publication failure after assignments")

        monkeypatch.setattr(editor_api, "publish_draft", fail)
        async with sessions() as session:
            with pytest.raises(HTTPException):
                await launch_editor(
                    ids.version,
                    LaunchDraft(expected_revision=expected, channel_ids=[ids.channel]),
                    user,
                    session,
                    "launch",
                )
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(FlowChannelAssignment).where(FlowChannelAssignment.flow_id == ids.flow)
                )
                is None
            )
            version = await session.get(ContentFlowVersion, ids.version)
            assert version is not None and version.status == "draft" and version.edit_revision == expected
        monkeypatch.setattr(editor_api, "publish_draft", original)

        async def launch() -> dict[str, Any]:
            async with sessions() as session:
                return await launch_editor(
                    ids.version,
                    LaunchDraft(expected_revision=expected, channel_ids=[ids.channel]),
                    user,
                    session,
                    "launch",
                )

        result, replay = await asyncio.gather(launch(), launch())
        assert result == replay and result["status"] == "published"
        async with sessions() as session:
            assignment = await session.scalar(
                select(FlowChannelAssignment).where(FlowChannelAssignment.flow_id == ids.flow)
            )
            assert assignment is not None and assignment.channel_id == ids.channel
            next_doc = await read_editor(ids.flow, user, session)
            assert next_doc["version"]["id"] != ids.version
            assert next_doc["flow"]["channel_ids"] == [ids.channel]
            changed = body(next_doc)
            changed.steps[0].payload = {"type": "text", "text": "New draft"}
            await save_editor(next_doc["version"]["id"], changed, user, session, "next-draft")
            published = await session.scalar(select(ContentStep).where(ContentStep.version_id == ids.version))
            assert published is not None and published.payload["text"] == "👋 {name} ⭐"


@PG
@pytest.mark.asyncio
async def test_legacy_sequence_and_attachment_payload_survive_copy_and_reorder() -> None:
    async with fixture() as (sessions, user, other, ids):
        sequence = {
            "sequence": [
                {"kind": "message", "payload": {"type": "text", "text": "Legacy", "entities": []}},
                {"kind": "attachment", "attachment_index": 0},
            ]
        }
        async with sessions() as session:
            await session.execute(
                update(ContentStep).where(ContentStep.id == ids.step).values(payload=sequence)
            )
            session.add(
                ContentAttachment(
                    step_id=ids.step,
                    position=0,
                    media_type="photo",
                    storage_key="catalog/shared.png",
                    payload={
                        "caption": "⭐",
                        "caption_entities": [
                            {"type": "custom_emoji", "offset": 0, "length": 1, "custom_emoji_id": "12345"}
                        ],
                    },
                )
            )
            await session.commit()
            doc = await read_editor(ids.flow, user, session)
            assert doc["steps"][0]["editor_read_only"]
            data = body(doc)
            data.steps.append(deepcopy(data.steps[0]))
            data.steps[0].delay_after_seconds = 900
            saved = await save_editor(ids.version, data, user, session, "copy")
            assert len(saved["steps"]) == 2
            for step in saved["steps"]:
                assert step["payload"] == sequence
                assert step["attachments"][0]["storage_key"] == "catalog/shared.png"
                assert step["attachments"][0]["payload"]["caption_entities"][0]["custom_emoji_id"] == "12345"
            changed = body(saved)
            changed.steps[0].payload = {"type": "text", "text": "flattened"}
            with pytest.raises(HTTPException) as rejected:
                await save_editor(ids.version, changed, user, session, "flatten")
            assert rejected.value.status_code == 422


@pytest.mark.asyncio
async def test_emoji_pack_and_proxy_hide_tokens_and_reject_external_animation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gzip
    import json

    from gramly_welcome import editor_assets

    editor_assets._set_cache.clear()
    editor_assets._asset_cache.clear()
    state: dict[str, Any] = {"kind": "custom_emoji", "animation": {"v": "5.5", "assets": []}}

    class FakeBot:
        def __init__(self, token: str) -> None:
            assert token == "123:secret"

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

        async def get_sticker_set(self, name: str) -> Any:
            assert name == "Test"
            return SimpleNamespace(
                name=name,
                title="Test set",
                sticker_type=state["kind"],
                stickers=[
                    SimpleNamespace(custom_emoji_id="123", emoji="⭐", is_animated=True, is_video=False)
                ],
            )

        async def get_custom_emoji_stickers(self, ids: list[str]) -> Any:
            return [SimpleNamespace(file_id="private", thumbnail=None)]

        async def get_file(self, file_id: str) -> Any:
            return SimpleNamespace(file_path="private.tgs", file_size=100)

        async def download_file(self, path: str, destination: Any) -> None:
            destination.write(gzip.compress(json.dumps(state["animation"]).encode()))

    monkeypatch.setattr(editor_assets, "Bot", FakeBot)
    settings = Settings(interface_bot_token="123:secret")
    pack = await editor_assets.emoji_set("https://t.me/addemoji/Test", settings)
    assert pack["items"] == [{"id": "123", "emoji": "⭐", "format": "tgs"}]
    assert "secret" not in str(pack) and "private" not in str(pack)
    response = await editor_assets.emoji_asset("123", False, settings)
    assert response.media_type == "application/json"
    editor_assets._asset_cache.clear()
    state["animation"] = {"assets": [{"u": "https://evil.test/", "p": "pixel.png"}]}
    with pytest.raises(HTTPException) as denied:
        await editor_assets.emoji_asset("123", False, settings)
    assert denied.value.status_code == 502
    editor_assets._set_cache.clear()
    state["kind"] = "regular"
    with pytest.raises(HTTPException) as wrong_pack:
        await editor_assets.emoji_set("https://t.me/addemoji/Test", settings)
    assert wrong_pack.value.status_code == 422


@PG
@pytest.mark.asyncio
async def test_admin_template_to_owner_launch_keeps_catalog_and_second_copy_immutable() -> None:
    from gramly_welcome.admin_api import AdminIdentity
    from gramly_welcome.admin_catalog import CatalogSave, publish_template, save_template
    from gramly_welcome.message_catalog import CatalogEntry, install_template
    from gramly_welcome.models import EventLog, MessageTemplate
    from gramly_welcome.personalization import personalize_operation

    async with fixture() as (sessions, user, other, ids):
        slug = "editor-integration-" + uuid.uuid4().hex
        admin = AdminIdentity(username="editor-test", groups={"gramly-owners"})
        payload: dict[str, Any] = {
            "type": "text",
            "text": "👋 {name} ⭐",
            "entities": [
                {"type": "bold", "offset": 3, "length": 6},
                {"type": "custom_emoji", "offset": 10, "length": 1, "custom_emoji_id": "12345"},
            ],
        }
        entry = CatalogEntry.model_validate(
            {
                "slug": slug,
                "revision": 1,
                "kind": "welcome",
                "title": "Integration",
                "description": "Synthetic test",
                "spec": {
                    "steps": [
                        {
                            "payload": payload,
                            "attachments": [
                                {"media_type": "photo", "storage_key": "catalog/shared.png", "payload": {}}
                            ],
                            "keyboard": {
                                "kind": "inline",
                                "rows": [
                                    [{"text": "Link", "action_type": "url", "value": "https://gramly.tech"}]
                                ],
                            },
                        }
                    ]
                },
            }
        )
        template_id = None
        try:
            async with sessions() as session:
                saved = await save_template(CatalogSave(entry=entry, expected_revision=0), admin, session)
                template_id = saved["id"]
                await publish_template(template_id, admin, session)
                flow = await session.get(ContentFlow, ids.flow)
                assert flow is not None
                copy = await install_template(
                    session, owner_id=ids.owner, bot_id=flow.bot_id, template_id=template_id, key="install1"
                )
                second = await install_template(
                    session, owner_id=ids.owner, bot_id=flow.bot_id, template_id=template_id, key="install2"
                )
                draft = await read_editor(copy["flow_id"], user, session)
                assert draft["flow"]["channel_ids"] == []
                data = body(draft)
                data.first_delay_seconds = 30
                data.steps[0].payload = {**payload, "text": payload["text"] + "!"}
                edited = await save_editor(copy["version_id"], data, user, session, "edit-copy")
                await launch_editor(
                    copy["version_id"],
                    LaunchDraft(
                        expected_revision=edited["version"]["edit_revision"], channel_ids=[ids.channel]
                    ),
                    user,
                    session,
                    "launch-copy",
                )
                template = await session.get(MessageTemplate, template_id)
                assert template is not None and template.spec == entry.spec.model_dump(mode="json")
                untouched = await read_editor(second["flow_id"], user, session)
                assert untouched["steps"][0]["payload"] == payload
                caption = edited["steps"][0]["attachments"][0]["payload"]
                rendered, _ = personalize_operation(caption, [], "Максим 🚀")
                assert rendered["caption"] == "👋 Максим 🚀 ⭐!"
                emoji = next(e for e in rendered["caption_entities"] if e["type"] == "custom_emoji")
                assert emoji["custom_emoji_id"] == "12345" and emoji["offset"] == 13
        finally:
            async with sessions() as session:
                await session.execute(
                    delete(EventLog).where(EventLog.context["template_id"].as_integer() == template_id)
                )
                await session.execute(delete(MessageTemplate).where(MessageTemplate.slug == slug))
                await session.commit()
