from __future__ import annotations

import io
import logging
from pathlib import PurePath

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BufferedInputFile,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    MessageEntity,
)
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

DOWNLOADABLE_TYPES = (
    "animation",
    "audio",
    "document",
    "photo",
    "sticker",
    "video",
    "video_note",
    "voice",
)


def make_bot(token: str, *, html: bool = False) -> Bot:
    defaults = DefaultBotProperties(parse_mode=ParseMode.HTML) if html else None
    return Bot(token=token, default=defaults)


def _entity_dump(entities) -> list[dict]:
    return [entity.model_dump(mode="json", exclude_none=True) for entity in (entities or [])]


def serialize_message(message: Message) -> dict:
    """Serialize user-visible Telegram content, excluding chat/sender metadata."""
    payload = {
        "type": message.content_type,
        "text": message.text,
        "caption": message.caption,
        "entities": _entity_dump(message.entities),
        "caption_entities": _entity_dump(message.caption_entities),
        "has_spoiler": bool(message.has_media_spoiler),
    }
    if message.location:
        payload["location"] = message.location.model_dump(mode="json", exclude_none=True)
    if message.contact:
        payload["contact"] = message.contact.model_dump(mode="json", exclude_none=True)
    if message.venue:
        payload["venue"] = message.venue.model_dump(mode="json", exclude_none=True)
    if message.dice:
        payload["dice"] = message.dice.model_dump(mode="json", exclude_none=True)
    if message.poll:
        payload["poll"] = message.poll.model_dump(mode="json", exclude_none=True)
    return {key: value for key, value in payload.items() if value not in (None, [], {})}


def downloadable(message: Message):
    kind = message.content_type
    if kind == "photo" and message.photo:
        return kind, message.photo[-1], "photo.jpg", "image/jpeg"
    obj = getattr(message, kind, None) if kind in DOWNLOADABLE_TYPES else None
    if obj is None:
        return None
    name = getattr(obj, "file_name", "") or f"{kind}.bin"
    mime = getattr(obj, "mime_type", "") or "application/octet-stream"
    return kind, obj, PurePath(name).name, mime


async def download_message_media(bot: Bot, message: Message, storage_prefix: str) -> dict | None:
    item = downloadable(message)
    if not item:
        return None
    kind, telegram_file, filename, mime = item
    size = int(getattr(telegram_file, "file_size", 0) or 0)
    if size and size > settings.WELCOME_MEDIA_MAX_BYTES:
        raise ValueError("Файл превышает доступный Telegram-боту лимит загрузки 20 МБ.")

    target = io.BytesIO()
    await bot.download(telegram_file, destination=target)
    body = target.getvalue()
    if len(body) > settings.WELCOME_MEDIA_MAX_BYTES:
        raise ValueError("Файл превышает доступный Telegram-боту лимит загрузки 20 МБ.")
    key = f"{storage_prefix}/{filename}"
    saved_key = await sync_to_async(default_storage.save)(key, ContentFile(body))
    return {
        "media_type": kind,
        "storage_key": saved_key,
        "original_name": filename,
        "mime_type": mime,
        "size": len(body),
    }


def _entities(raw: list[dict] | None) -> list[MessageEntity] | None:
    return [MessageEntity.model_validate(entity) for entity in raw] if raw else None


async def _stored_file(media) -> BufferedInputFile:
    def read() -> bytes:
        with default_storage.open(media.storage_key, "rb") as handle:
            return handle.read()

    return BufferedInputFile(await sync_to_async(read)(), filename=media.original_name or "media.bin")


async def send_saved_message(bot: Bot, chat_id: int, version) -> None:
    """Replay one stored message through a customer bot without parse-mode loss."""
    payload = version.payload
    kind = payload.get("type", "text")
    media_items = await sync_to_async(list)(version.media.order_by("position", "id"))
    media = media_items[0] if media_items else None
    common_caption = {
        "caption": payload.get("caption"),
        "caption_entities": _entities(payload.get("caption_entities")),
    }
    common_caption = {k: v for k, v in common_caption.items() if v is not None}

    if kind == "media_group":
        inputs = []
        item_payloads = sorted(payload.get("items", []), key=lambda item: item.get("telegram_message_id", 0))
        classes = {
            "photo": InputMediaPhoto,
            "video": InputMediaVideo,
            "audio": InputMediaAudio,
            "document": InputMediaDocument,
        }
        for index, stored in enumerate(media_items):
            item = item_payloads[index] if index < len(item_payloads) else {}
            cls = classes.get(stored.media_type)
            if not cls:
                raise ValueError(f"Unsupported album media type: {stored.media_type}")
            media_kwargs = {
                "media": await _stored_file(stored),
                "caption": item.get("caption"),
                "caption_entities": _entities(item.get("caption_entities")),
            }
            if stored.media_type in {"photo", "video"}:
                media_kwargs["has_spoiler"] = bool(item.get("has_spoiler"))
            inputs.append(cls(**media_kwargs))
        if not inputs:
            raise ValueError("Stored media group is empty")
        await bot.send_media_group(chat_id, media=inputs)
        return
    if kind == "text":
        await bot.send_message(
            chat_id,
            payload.get("text", ""),
            entities=_entities(payload.get("entities")),
        )
        return
    if media:
        upload = await _stored_file(media)
        kwargs = dict(common_caption)
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
        if method:
            if kind in {"video_note", "sticker"}:
                kwargs = {}
            await method(chat_id, upload, **kwargs)
            return
    if kind == "location":
        location = payload["location"]
        await bot.send_location(chat_id, location["latitude"], location["longitude"])
    elif kind == "contact":
        contact = payload["contact"]
        await bot.send_contact(chat_id, contact["phone_number"], contact["first_name"], last_name=contact.get("last_name"))
    elif kind == "venue":
        venue = payload["venue"]
        loc = venue["location"]
        await bot.send_venue(chat_id, loc["latitude"], loc["longitude"], venue["title"], venue["address"])
    elif kind == "dice":
        await bot.send_dice(chat_id, emoji=payload["dice"].get("emoji"))
    elif kind == "poll":
        poll = payload["poll"]
        options = [item["text"] for item in poll.get("options", [])]
        await bot.send_poll(chat_id, poll["question"], options, is_anonymous=poll.get("is_anonymous", True))
    else:
        raise ValueError(f"Unsupported saved Telegram content type: {kind}")


async def configure_customer_webhook(managed_bot, *, drop_pending: bool = False) -> None:
    url = f"{settings.WELCOME_CLIENT_WEBHOOK_BASE_URL.rstrip('/')}/{managed_bot.public_id}/{managed_bot.path_secret}/"
    bot = make_bot(managed_bot.get_token())
    try:
        await bot.set_webhook(
            url,
            secret_token=managed_bot.webhook_secret,
            allowed_updates=["message", "my_chat_member", "chat_member", "chat_join_request"],
            drop_pending_updates=drop_pending,
            max_connections=20,
        )
    finally:
        await bot.session.close()


async def remove_customer_webhook(managed_bot) -> None:
    bot = make_bot(managed_bot.get_token())
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    finally:
        await bot.session.close()
