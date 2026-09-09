from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Chat, Message, MessageEntity

from gramly_welcome.crypto import TokenKeyring
from gramly_welcome.repository import DeliveryContext
from gramly_welcome.storage import ObjectStorage
from gramly_welcome.telegram_delivery import send_compiled_operation, send_greeting


def sent_message(message_id: int, **kwargs: Any) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=42, type="private"),
        **kwargs,
    )


def media_storage(tmp_path: Path) -> ObjectStorage:
    @asynccontextmanager
    async def materialize(_media: Any) -> AsyncIterator[Path]:
        yield tmp_path / "test-media"

    return cast(ObjectStorage, SimpleNamespace(materialize=materialize))


@pytest.mark.parametrize("kind", ["photo", "video", "animation", "audio", "document", "voice"])
async def test_single_caption_keeps_explicit_entities_with_html_default(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entities = [
        MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id="12345"),
        MessageEntity(type="bold", offset=3, length=4),
    ]
    bot = Bot("123456:token", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    sender = AsyncMock(return_value=sent_message(101, caption="🛴 bold", caption_entities=entities))
    monkeypatch.setattr(bot, f"send_{kind}", sender)
    result = await send_compiled_operation(
        bot, 42, kind, {},
        [{
            "storage_key": "test", "media_type": kind,
            "payload": {
                "caption": "🛴 bold",
                "caption_entities": [entity.model_dump(exclude_none=True) for entity in entities],
            },
        }],
        media_storage(tmp_path),
    )
    assert "parse_mode" in sender.call_args.kwargs
    assert sender.call_args.kwargs["parse_mode"] is None
    assert sender.call_args.kwargs["caption_entities"] == entities
    assert result.message_ids == (101,)
    assert result.requested == 1
    assert result.preserved


async def test_album_receipt_includes_every_message_and_disables_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = Bot("123456:token", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    sender = AsyncMock(return_value=[sent_message(101), sent_message(102)])
    monkeypatch.setattr(bot, "send_media_group", sender)
    result = await send_compiled_operation(
        bot, 42, "media_group", {},
        [{"storage_key": str(i), "media_type": "photo", "payload": {"caption": "<plain>"}}
         for i in range(2)],
        media_storage(tmp_path),
    )
    assert all(item.parse_mode is None for item in sender.call_args.kwargs["media"])
    assert result.message_ids == (101, 102)


async def test_native_sticker_receipt_does_not_materialize_media(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot("123456:token")
    monkeypatch.setattr(
        "gramly_welcome.telegram_delivery.resolve_native_sticker", AsyncMock(return_value="native-file")
    )
    monkeypatch.setattr(bot, "send_sticker", AsyncMock(return_value=sent_message(103)))
    result = await send_compiled_operation(
        bot, 42, "sticker", {}, [{"payload": {}}], cast(ObjectStorage, SimpleNamespace())
    )
    assert result.message_ids == (103,)


async def test_preview_receipt_is_registered_before_media_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def materialize(_media: Any) -> AsyncIterator[Path]:
        yield tmp_path / "test-media"
        raise OSError("cleanup failed")

    bot = Bot("123456:token")
    monkeypatch.setattr(bot, "send_photo", AsyncMock(return_value=sent_message(107)))
    on_sent = AsyncMock()
    with pytest.raises(OSError, match="cleanup failed"):
        await send_compiled_operation(
            bot, 42, "photo", {}, [{"storage_key": "test", "media_type": "photo"}],
            cast(ObjectStorage, SimpleNamespace(materialize=materialize)), on_sent=on_sent,
        )
    on_sent.assert_awaited_once_with((107,))


@pytest.mark.parametrize("kind", ["video_note", "sticker"])
async def test_noncaption_media_receipt_does_not_add_parse_mode(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = Bot("123456:token")
    sender = AsyncMock(return_value=sent_message(105))
    monkeypatch.setattr(bot, f"send_{kind}", sender)
    result = await send_compiled_operation(
        bot, 42, kind, {}, [{"storage_key": "test", "media_type": kind}], media_storage(tmp_path)
    )
    assert result.message_ids == (105,)
    assert "parse_mode" not in sender.call_args.kwargs


async def test_legacy_caption_does_not_restore_default_html_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = Bot("123456:token", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    sender = AsyncMock(return_value=sent_message(106))
    monkeypatch.setattr(bot, "send_photo", sender)
    monkeypatch.setattr("gramly_welcome.telegram_delivery.Bot", lambda **_kwargs: bot)
    entity = {"type": "bold", "offset": 0, "length": 4}
    context = SimpleNamespace(
        bot=SimpleNamespace(token_ciphertext="encrypted"),
        contact=SimpleNamespace(first_name="Test", telegram_id=42),
        version=SimpleNamespace(payload={"type": "photo", "caption": "bold", "caption_entities": [entity]}),
        media=[SimpleNamespace(storage_key="test", original_name="test.jpg", size=0, media_type="photo")],
    )
    await send_greeting(
        cast(DeliveryContext, context), media_storage(tmp_path),
        cast(TokenKeyring, SimpleNamespace(decrypt=lambda _: "123456:token")),
    )
    assert sender.call_args.kwargs["parse_mode"] is None
    assert sender.call_args.kwargs["caption_entities"] == [MessageEntity.model_validate(entity)]


@pytest.mark.parametrize("kind,payload", [
    ("text", {"text": "hello"}),
    ("location", {"location": {"latitude": 1, "longitude": 2}}),
    ("contact", {"contact": {"phone_number": "+10000000000", "first_name": "Test"}}),
    ("venue", {"venue": {"location": {"latitude": 1, "longitude": 2}, "title": "Test", "address": "Test"}}),
    ("dice", {"dice": {"emoji": "🎲"}}),
    ("poll", {"poll": {"question": "Test?", "options": [{"text": "Yes"}, {"text": "No"}]}}),
])
async def test_nonmedia_receipts_are_available_without_premium(
    kind: str, payload: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = Bot("123456:token")
    monkeypatch.setattr(
        bot, "send_message" if kind == "text" else f"send_{kind}",
        AsyncMock(return_value=sent_message(104)),
    )
    result = await send_compiled_operation(
        bot, 42, kind, payload, [], cast(ObjectStorage, SimpleNamespace())
    )
    assert result.message_ids == (104,)
    assert result.requested == 0
