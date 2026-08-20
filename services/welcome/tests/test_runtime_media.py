from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from aiogram.types import Message, MessageEntity

from gramly_welcome.owner_bot import _downloadable, serialize_message


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


def test_event_worker_has_ephemeral_media_workspace() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    manifest = (
        repo_root / "infra/kubernetes/base/welcome-runtime/workers.yaml"
    ).read_text(encoding="utf-8")
    event_worker = manifest.split("---", maxsplit=1)[0]

    assert "readOnlyRootFilesystem: true" in event_worker
    assert "name: temporary-media, mountPath: /tmp" in event_worker
    assert "name: temporary-media, emptyDir:" in event_worker
