from __future__ import annotations

from gramly_welcome.content_compiler import AttachmentSpec, compile_step


def media(kind: str, index: int) -> AttachmentSpec:
    return AttachmentSpec(kind, f"objects/{index}", f"file-{index}.{kind}")


def test_compiles_mixed_media_into_telegram_legal_groups() -> None:
    operations = compile_step(
        {"type": "media_group", "items": []},
        [
            media("photo", 1),
            media("video", 2),
            media("document", 3),
            media("document", 4),
            media("audio", 5),
            media("audio", 6),
            media("sticker", 7),
        ],
    )

    assert [item.operation_type for item in operations] == [
        "media_group",
        "media_group",
        "media_group",
        "sticker",
    ]
    assert [[entry["media_type"] for entry in item.media] for item in operations] == [
        ["photo", "video"],
        ["document", "document"],
        ["audio", "audio"],
        ["sticker"],
    ]


def test_splits_media_groups_at_ten_items() -> None:
    operations = compile_step(
        {"type": "media_group", "items": []},
        [media("photo", index) for index in range(21)],
    )

    assert [len(item.media) for item in operations] == [10, 10, 1]
    assert operations[-1].operation_type == "photo"


def test_keyboard_detaches_last_album_item() -> None:
    keyboard = {
        "kind": "inline",
        "rows": [[{"text": "Open", "action_type": "url", "value": "https://gramly.tech"}]],
    }
    operations = compile_step(
        {"type": "media_group", "items": []},
        [media("photo", 1), media("video", 2), media("photo", 3)],
        keyboard,
    )

    assert [item.operation_type for item in operations] == ["media_group", "photo"]
    assert len(operations[0].media) == 2
    assert operations[1].payload["keyboard"] == keyboard


def test_two_item_album_with_keyboard_becomes_two_independent_calls() -> None:
    keyboard = {"kind": "reply", "rows": [[{"text": "Continue"}]]}
    operations = compile_step(
        {"type": "media_group", "items": []},
        [media("photo", 1), media("video", 2)],
        keyboard,
    )

    assert [item.operation_type for item in operations] == ["photo", "video"]
    assert "keyboard" not in operations[0].payload
    assert operations[1].payload["keyboard"] == keyboard


def test_preserves_premium_emoji_entities_without_rewriting_offsets() -> None:
    entity = {
        "type": "custom_emoji",
        "offset": 7,
        "length": 2,
        "custom_emoji_id": "5368324170671202286",
    }
    operations = compile_step({"type": "text", "text": "Привет ✨", "entities": [entity]}, [])

    assert operations[0].payload["entities"] == [entity]


def test_text_becomes_first_attachment_caption() -> None:
    operations = compile_step(
        {"type": "text", "text": "Привет", "entities": []},
        [media("photo", 1)],
    )

    assert operations[0].operation_type == "photo"
    assert operations[0].payload["caption"] == "Привет"
