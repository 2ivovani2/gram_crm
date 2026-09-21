from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiogram import Bot
from aiogram.types import LinkPreviewOptions, Message, MessageEntity

from gramly_welcome.owner_bot import _downloadable, _native_sticker_metadata, serialize_message
from gramly_welcome.storage import ObjectStorage
from gramly_welcome.telegram_delivery import (
    _native_sticker_cache,
    bind_subscribe_links,
    observe_custom_emoji_delivery,
    resolve_native_sticker,
    send_compiled_operation,
)


def test_subscribe_link_is_bound_to_delivery_channel_without_mutating_snapshot() -> None:
    from gramly_welcome.audience import build_subscribe_token, verify_subscribe_token

    secret = "bot-secret"
    original: dict[str, Any] = {
        "keyboard": {
            "kind": "inline",
            "rows": [[{
                "text": "Добавить в базу",
                "action_type": "url",
                "value": f"https://t.me/example_bot?start={build_subscribe_token(42, 0, secret)}",
            }]],
        }
    }

    bound = bind_subscribe_links(
        original, bot_username="example_bot", path_secret=secret, channel_id=17
    )

    value = bound["keyboard"]["rows"][0][0]["value"]
    assert verify_subscribe_token(value.split("?start=", 1)[1], secret) == (42, 17)
    assert verify_subscribe_token(
        original["keyboard"]["rows"][0][0]["value"].split("?start=", 1)[1], secret
    ) == (42, 0)


@pytest.mark.parametrize(
    ("kind", "attributes", "expected_name", "expected_mime"),
    [
        ("video", {}, "video.mp4", "video/mp4"),
        ("video_note", {}, "video-note.mp4", "video/mp4"),
        ("voice", {}, "voice.ogg", "audio/ogg"),
        ("sticker", {}, "sticker.webp", "image/webp"),
        ("sticker", {"is_animated": True}, "sticker.tgs", "application/x-tgsticker"),
        ("sticker", {"is_video": True}, "sticker.webm", "video/webm"),
    ],
)
def test_downloadable_uses_telegram_compatible_filenames(
    kind: str,
    attributes: dict[str, object],
    expected_name: str,
    expected_mime: str,
) -> None:
    media = SimpleNamespace(file_name=None, mime_type=None, **attributes)
    message = SimpleNamespace(content_type=kind, photo=None, **{kind: media})

    result = _downloadable(cast(Message, message))

    assert result is not None
    assert result[2:] == (expected_name, expected_mime)


def test_serialize_message_keeps_custom_emoji_id_and_utf16_offsets() -> None:
    entity = MessageEntity(
        type="custom_emoji",
        offset=7,
        length=2,
        custom_emoji_id="5368324170671202286",
    )
    message = SimpleNamespace(
        content_type="text",
        text="Привет ✨",
        caption=None,
        entities=[entity],
        caption_entities=None,
        has_media_spoiler=False,
        location=None,
        contact=None,
        venue=None,
        dice=None,
        poll=None,
    )

    payload = serialize_message(cast(Message, message))

    assert payload["entities"] == [
        {
            "type": "custom_emoji",
            "offset": 7,
            "length": 2,
            "custom_emoji_id": "5368324170671202286",
        }
    ]


@pytest.mark.asyncio
async def test_compiled_text_disables_default_parse_mode_and_preserves_custom_emoji() -> None:
    entity = MessageEntity(
        type="custom_emoji",
        offset=0,
        length=2,
        custom_emoji_id="5368324170671202286",
    )
    calls: dict[str, object] = {}

    async def send_message(_chat_id: int, _text: str, **kwargs: object) -> Message:
        calls.update(kwargs)
        return cast(Message, SimpleNamespace(entities=[entity], caption_entities=None))

    bot = cast(Bot, SimpleNamespace(token="123:secret", send_message=send_message))
    result = await send_compiled_operation(
        bot,
        42,
        "text",
        {
            "text": "✨",
            "entities": [entity.model_dump(mode="json", exclude_none=True)],
        },
        [],
        cast(ObjectStorage, SimpleNamespace()),
    )

    assert calls["parse_mode"] is None
    assert result.preserved
    assert result.requested == 1


@pytest.mark.asyncio
async def test_compiled_text_applies_per_message_link_preview_setting() -> None:
    calls: dict[str, object] = {}

    async def send_message(_chat_id: int, _text: str, **kwargs: object) -> Message:
        calls.update(kwargs)
        return cast(Message, SimpleNamespace(entities=[], caption_entities=None))

    bot = cast(Bot, SimpleNamespace(token="123:secret", send_message=send_message))
    await send_compiled_operation(
        bot,
        42,
        "text",
        {"text": "https://gramly.tech", "link_preview_enabled": False},
        [],
        cast(ObjectStorage, SimpleNamespace()),
    )

    options = calls["link_preview_options"]
    assert isinstance(options, LinkPreviewOptions)
    assert options.is_disabled is True


def test_custom_emoji_downgrade_is_detected_from_telegram_response() -> None:
    bot = cast(Bot, SimpleNamespace(token="123:secret"))
    message = cast(Message, SimpleNamespace(entities=[], caption_entities=None))

    result = observe_custom_emoji_delivery(bot, ["premium-1"], [message])

    assert result.downgraded


def test_native_sticker_metadata_keeps_premium_identity() -> None:
    sticker = SimpleNamespace(
        type="custom_emoji",
        custom_emoji_id="emoji-42",
        set_name="PremiumSet",
        file_unique_id="stable-file",
        emoji="✨",
        is_animated=True,
        is_video=False,
        premium_animation=SimpleNamespace(file_id="premium-animation"),
    )

    result = _native_sticker_metadata(cast(Message, SimpleNamespace(sticker=sticker)))

    assert result == {
        "type": "custom_emoji",
        "custom_emoji_id": "emoji-42",
        "set_name": "PremiumSet",
        "file_unique_id": "stable-file",
        "emoji": "✨",
        "format": "animated",
        "premium_animation": True,
    }


@pytest.mark.asyncio
async def test_native_custom_emoji_is_resolved_per_sending_bot() -> None:
    calls = 0

    async def get_custom_emoji_stickers(ids: list[str]) -> list[SimpleNamespace]:
        nonlocal calls
        calls += 1
        assert ids == ["emoji-42"]
        return [SimpleNamespace(custom_emoji_id="emoji-42", file_id="bot-scoped-file")]

    bot = SimpleNamespace(token="123:secret", get_custom_emoji_stickers=get_custom_emoji_stickers)
    media = {"payload": {"telegram_native": {"custom_emoji_id": "emoji-42"}}}
    _native_sticker_cache.clear()

    first = await resolve_native_sticker(cast(Bot, bot), media)
    second = await resolve_native_sticker(cast(Bot, bot), media)

    assert first == second == "bot-scoped-file"
    assert calls == 1


@pytest.mark.asyncio
async def test_native_premium_sticker_uses_set_file_unique_id() -> None:
    async def get_sticker_set(name: str) -> SimpleNamespace:
        assert name == "PremiumSet"
        return SimpleNamespace(
            stickers=[
                SimpleNamespace(file_unique_id="other", file_id="wrong"),
                SimpleNamespace(file_unique_id="stable-file", file_id="bot-file"),
            ]
        )

    bot = SimpleNamespace(token="456:secret", get_sticker_set=get_sticker_set)
    media = {
        "payload": {
            "telegram_native": {
                "set_name": "PremiumSet",
                "file_unique_id": "stable-file",
            }
        }
    }
    _native_sticker_cache.clear()

    result = await resolve_native_sticker(cast(Bot, bot), media)

    assert result == "bot-file"


@pytest.mark.asyncio
async def test_legacy_sticker_keeps_object_storage_fallback() -> None:
    bot = SimpleNamespace(token="789:secret")

    assert await resolve_native_sticker(cast(Bot, bot), {"payload": {}}) is None


def test_event_worker_has_ephemeral_media_workspace() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    manifest = (
        repo_root / "infra/kubernetes/base/welcome-runtime/workers.yaml"
    ).read_text(encoding="utf-8")
    event_worker = manifest.split("---", maxsplit=1)[0]

    assert "readOnlyRootFilesystem: true" in event_worker
    assert "name: temporary-media, mountPath: /tmp" in event_worker
    assert "name: temporary-media, emptyDir:" in event_worker
