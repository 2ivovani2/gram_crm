from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from aiogram import Bot
from aiogram.types import Message, MessageEntity

from gramly_welcome.owner_bot import _downloadable, _native_sticker_metadata, serialize_message
from gramly_welcome.telegram_delivery import _native_sticker_cache, resolve_native_sticker


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
