from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any, cast

from aiogram import Bot
from aiogram.types import (
    FSInputFile,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    MessageEntity,
)

from .crypto import TokenKeyring
from .repository import DeliveryContext, JoinRequestContext
from .storage import ObjectStorage


def _entities(raw: list[dict[str, Any]] | None) -> list[MessageEntity] | None:
    return [MessageEntity.model_validate(entity) for entity in raw] if raw else None


async def send_greeting(
    context: DeliveryContext, storage: ObjectStorage, keyring: TokenKeyring
) -> None:
    token = keyring.decrypt(context.bot.token_ciphertext)
    payload = context.version.payload
    kind = str(payload.get("type") or "text")
    async with AsyncExitStack() as stack:
        paths = [
            await stack.enter_async_context(storage.materialize(media)) for media in context.media
        ]
        uploads = [
            FSInputFile(path, filename=media.original_name or "media.bin")
            for path, media in zip(paths, context.media, strict=True)
        ]
        async with Bot(token=token) as bot:
            if kind == "media_group":
                item_payloads = sorted(
                    payload.get("items", []), key=lambda item: item.get("telegram_message_id", 0)
                )
                classes = {
                    "photo": InputMediaPhoto,
                    "video": InputMediaVideo,
                    "audio": InputMediaAudio,
                    "document": InputMediaDocument,
                }
                album = []
                for index, (media, upload) in enumerate(zip(context.media, uploads, strict=True)):
                    item = item_payloads[index] if index < len(item_payloads) else {}
                    media_class = classes.get(media.media_type)
                    if media_class is None:
                        raise ValueError("Unsupported album media type")
                    kwargs: dict[str, Any] = {
                        "media": upload,
                        "caption": item.get("caption"),
                        "caption_entities": _entities(item.get("caption_entities")),
                    }
                    if media.media_type in {"photo", "video"}:
                        kwargs["has_spoiler"] = bool(item.get("has_spoiler"))
                    album.append(media_class(**kwargs))
                if not album:
                    raise ValueError("Stored media group is empty")
                await bot.send_media_group(context.contact.telegram_id, media=album)
                return
            if kind == "text":
                await bot.send_message(
                    context.contact.telegram_id,
                    str(payload.get("text") or ""),
                    entities=_entities(payload.get("entities")),
                )
                return
            if uploads:
                upload = uploads[0]
                kwargs = {
                    "caption": payload.get("caption"),
                    "caption_entities": _entities(payload.get("caption_entities")),
                }
                kwargs = {key: value for key, value in kwargs.items() if value is not None}
                if kind in {"photo", "video", "animation"}:
                    kwargs["has_spoiler"] = bool(payload.get("has_spoiler"))
                method = {
                    "photo": bot.send_photo,
                    "video": bot.send_video,
                    "animation": bot.send_animation,
                    "audio": bot.send_audio,
                    "document": bot.send_document,
                    "voice": bot.send_voice,
                    "video_note": bot.send_video_note,
                    "sticker": bot.send_sticker,
                }.get(kind)
                if method is not None:
                    sender = cast(Callable[..., Awaitable[Any]], method)
                    await sender(
                        context.contact.telegram_id,
                        upload,
                        **({} if kind in {"video_note", "sticker"} else kwargs),
                    )
                    return
            if kind == "location":
                location = payload["location"]
                await bot.send_location(
                    context.contact.telegram_id, location["latitude"], location["longitude"]
                )
            elif kind == "contact":
                contact = payload["contact"]
                await bot.send_contact(
                    context.contact.telegram_id,
                    contact["phone_number"],
                    contact["first_name"],
                    last_name=contact.get("last_name"),
                )
            elif kind == "venue":
                venue = payload["venue"]
                location = venue["location"]
                await bot.send_venue(
                    context.contact.telegram_id,
                    location["latitude"],
                    location["longitude"],
                    venue["title"],
                    venue["address"],
                )
            elif kind == "dice":
                await bot.send_dice(context.contact.telegram_id, emoji=payload["dice"].get("emoji"))
            elif kind == "poll":
                poll = payload["poll"]
                await bot.send_poll(
                    context.contact.telegram_id,
                    poll["question"],
                    [option["text"] for option in poll.get("options", [])],
                    is_anonymous=poll.get("is_anonymous", True),
                )
            else:
                raise ValueError("Unsupported saved Telegram content type")


async def approve_join_request(context: JoinRequestContext, keyring: TokenKeyring) -> None:
    token = keyring.decrypt(context.bot.token_ciphertext)
    async with Bot(token=token) as bot:
        await bot.approve_chat_join_request(
            context.channel.telegram_id, context.contact.telegram_id
        )
