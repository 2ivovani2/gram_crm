from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, cast

from aiogram import Bot
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    KeyboardButton,
    MessageEntity,
    ReplyKeyboardMarkup,
)

from .crypto import TokenKeyring
from .flow_delivery import OperationContext
from .repository import DeliveryContext, JoinRequestContext
from .rotation import RotationContext, RotationDestination
from .storage import ObjectStorage


async def create_rotation_invite_link(
    destination: RotationDestination, keyring: TokenKeyring
) -> str:
    token = keyring.decrypt(destination.bot.token_ciphertext)
    async with Bot(token=token) as bot:
        result = await bot.create_chat_invite_link(
            destination.channel.telegram_id,
            name=destination.rotation.invite_link_name,
            creates_join_request=False,
        )
    return result.invite_link


async def send_rotation_recommendation(
    context: RotationContext,
    destinations: list[RotationDestination],
    keyring: TokenKeyring,
) -> None:
    token = keyring.decrypt(context.source_bot.token_ciphertext)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=destination.channel.title[:64], url=destination.rotation.invite_link)]
            for destination in destinations
        ]
    )
    async with Bot(token=token) as bot:
        await bot.send_message(
            context.contact.telegram_id,
            "Возможно, Вам будут интересны эти каналы:",
            reply_markup=keyboard,
        )


def _entities(raw: list[dict[str, Any]] | None) -> list[MessageEntity] | None:
    return [MessageEntity.model_validate(entity) for entity in raw] if raw else None


@dataclass
class _OperationMedia:
    storage_key: str
    original_name: str
    size: int


def _reply_markup(raw: dict[str, Any] | None) -> InlineKeyboardMarkup | ReplyKeyboardMarkup | None:
    if not raw:
        return None
    rows = raw.get("rows")
    if not isinstance(rows, list) or not rows:
        return None
    if raw.get("kind") == "reply":
        keyboard = [
            [KeyboardButton(text=str(button.get("text") or "")) for button in row]
            for row in rows
            if isinstance(row, list) and row
        ]
        raw_settings = raw.get("settings")
        settings: dict[str, Any] = raw_settings if isinstance(raw_settings, dict) else {}
        return ReplyKeyboardMarkup(
            keyboard=keyboard,
            resize_keyboard=bool(settings.get("resize_keyboard", True)),
            one_time_keyboard=bool(settings.get("one_time_keyboard", False)),
            is_persistent=bool(settings.get("is_persistent", False)),
        )
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    supports_style = "style" in InlineKeyboardButton.model_fields
    for row in rows:
        if not isinstance(row, list):
            continue
        result_row = []
        for button in row:
            if not isinstance(button, dict):
                continue
            action = str(button.get("action_type") or "callback")
            value = str(button.get("value") or "")
            values: dict[str, Any] = {"text": str(button.get("text") or "")}
            if action == "url":
                values["url"] = value
            else:
                values["callback_data"] = value
            if supports_style and button.get("style") in {"primary", "success", "danger"}:
                values["style"] = button["style"]
            result_row.append(InlineKeyboardButton(**values))
        if result_row:
            keyboard_rows.append(result_row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows) if keyboard_rows else None


async def send_delivery_operation(
    context: OperationContext, storage: ObjectStorage, keyring: TokenKeyring
) -> None:
    token = keyring.decrypt(context.bot.token_ciphertext)
    async with Bot(token=token) as bot:
        await send_compiled_operation(
            bot,
            context.target_chat_id,
            context.operation.operation_type,
            context.operation.payload,
            context.operation.media,
            storage,
        )


async def send_compiled_operation(
    bot: Bot,
    chat_id: int,
    operation_type: str,
    payload: dict[str, Any],
    raw_media: list[dict[str, Any]],
    storage: ObjectStorage,
) -> None:
    """Send one compiled operation with either a client bot or the preview bot."""
    media_objects = [
        _OperationMedia(
            storage_key=str(item["storage_key"]),
            original_name=str(item.get("original_name") or "media.bin"),
            size=int(item.get("size") or 0),
        )
        for item in raw_media
    ]
    async with AsyncExitStack() as stack:
        paths = [await stack.enter_async_context(storage.materialize(media)) for media in media_objects]
        uploads = [
            FSInputFile(path, filename=media.original_name)
            for path, media in zip(paths, media_objects, strict=True)
        ]
        markup = _reply_markup(payload.get("keyboard") if isinstance(payload.get("keyboard"), dict) else None)
        if operation_type == "media_group":
            classes = {
                "photo": InputMediaPhoto,
                "video": InputMediaVideo,
                "audio": InputMediaAudio,
                "document": InputMediaDocument,
            }
            album = []
            for item, upload in zip(raw_media, uploads, strict=True):
                media_type = str(item["media_type"])
                media_class = classes.get(media_type)
                if media_class is None:
                    raise ValueError("Unsupported album media type")
                raw_item_payload = item.get("payload")
                item_payload: dict[str, Any] = raw_item_payload if isinstance(raw_item_payload, dict) else {}
                album_kwargs: dict[str, Any] = {
                    "media": upload,
                    "caption": item_payload.get("caption"),
                    "caption_entities": _entities(item_payload.get("caption_entities")),
                }
                if media_type in {"photo", "video"}:
                    album_kwargs["has_spoiler"] = bool(item_payload.get("has_spoiler"))
                album.append(media_class(**album_kwargs))
            if len(album) < 2:
                raise ValueError("Telegram media groups require at least two items")
            await bot.send_media_group(chat_id, media=album)
            return
        kind = operation_type
        if kind == "text":
            await bot.send_message(
                chat_id,
                str(payload.get("text") or ""),
                entities=_entities(payload.get("entities")),
                reply_markup=markup,
            )
            return
        if uploads:
            upload = uploads[0]
            item = raw_media[0]
            raw_item_payload = item.get("payload")
            item_payload = raw_item_payload if isinstance(raw_item_payload, dict) else {}
            media_kwargs: dict[str, Any] = {
                "caption": item_payload.get("caption"),
                "caption_entities": _entities(item_payload.get("caption_entities")),
                "reply_markup": markup,
            }
            media_kwargs = {key: value for key, value in media_kwargs.items() if value is not None}
            if kind in {"photo", "video", "animation"}:
                media_kwargs["has_spoiler"] = bool(item_payload.get("has_spoiler"))
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
                    chat_id,
                    upload,
                    **({"reply_markup": markup} if kind in {"video_note", "sticker"} else media_kwargs),
                )
                return
        if kind == "location":
            location = payload["location"]
            await bot.send_location(
                chat_id,
                location["latitude"],
                location["longitude"],
                reply_markup=markup,
            )
        elif kind == "contact":
            contact = payload["contact"]
            await bot.send_contact(
                chat_id,
                contact["phone_number"],
                contact["first_name"],
                last_name=contact.get("last_name"),
                reply_markup=markup,
            )
        elif kind == "venue":
            venue = payload["venue"]
            location = venue["location"]
            await bot.send_venue(
                chat_id,
                location["latitude"],
                location["longitude"],
                venue["title"],
                venue["address"],
                reply_markup=markup,
            )
        elif kind == "dice":
            await bot.send_dice(
                chat_id,
                emoji=payload["dice"].get("emoji"),
                reply_markup=markup,
            )
        elif kind == "poll":
            poll = payload["poll"]
            await bot.send_poll(
                chat_id,
                poll["question"],
                [option["text"] for option in poll.get("options", [])],
                is_anonymous=poll.get("is_anonymous", True),
                reply_markup=markup,
            )
        else:
            raise ValueError("Unsupported compiled Telegram operation")


async def send_greeting(context: DeliveryContext, storage: ObjectStorage, keyring: TokenKeyring) -> None:
    token = keyring.decrypt(context.bot.token_ciphertext)
    payload = context.version.payload
    kind = str(payload.get("type") or "text")
    async with AsyncExitStack() as stack:
        paths = [await stack.enter_async_context(storage.materialize(media)) for media in context.media]
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
        await bot.approve_chat_join_request(context.channel.telegram_id, context.contact.telegram_id)
