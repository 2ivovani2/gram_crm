from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic
from typing import Any, cast

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    KeyboardButton,
    LinkPreviewOptions,
    Message,
    MessageEntity,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from .audience import (
    ClientMessageContext,
    build_subscribe_token,
    captcha_callback,
    client_message_text,
    verify_subscribe_token,
)
from .crypto import TokenKeyring
from .flow_delivery import OperationContext
from .join_activation import ActivationContext, build_activation_token
from .metrics import (
    CUSTOM_EMOJI_DOWNGRADED,
    CUSTOM_EMOJI_PRESERVED,
    CUSTOM_EMOJI_TELEGRAM_ERROR,
    PREMIUM_MEDIA,
)
from .personalization import personalize_operation
from .repository import DeliveryContext, JoinRequestContext
from .rotation import RotationContext, RotationDestination
from .storage import ObjectStorage
from .telegram_client import bot_client


async def create_rotation_invite_link(
    destination: RotationDestination, keyring: TokenKeyring
) -> str:
    token = keyring.decrypt(destination.bot.token_ciphertext)
    async with bot_client(token, bot_factory=Bot) as bot:
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
    async with bot_client(token, bot_factory=Bot) as bot:
        await bot.send_message(
            context.contact.telegram_id,
            "Возможно, Вам будут интересны эти каналы:",
            reply_markup=keyboard,
        )


async def send_join_activation(
    context: ActivationContext,
    keyring: TokenKeyring,
    *,
    activation_base_url: str,
) -> None:
    if context.request.user_chat_id is None:
        raise ValueError("join request has no temporary chat")
    token = build_activation_token(context.activation.public_id, context.bot.path_secret)
    activation_url = (
        f"{activation_base_url.rstrip('/')}/{context.bot.public_id}/{token}"
    )
    fallback_url = f"https://t.me/{context.bot.username}?start=gramly_{token}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Продолжить и вступить",
                    web_app=WebAppInfo(url=activation_url),
                )
            ],
            [InlineKeyboardButton(text="↗️ Открыть бота", url=fallback_url)],
        ]
    )
    message = (
        f"👋 <b>Ваша заявка в «{context.channel.title}» получена.</b>\n\n"
        "Чтобы бот мог продолжить знакомство после вступления, разрешите ему "
        "отправлять сообщения. Это займёт несколько секунд."
    )
    bot_token = keyring.decrypt(context.bot.token_ciphertext)
    async with bot_client(bot_token, bot_factory=Bot) as bot:
        try:
            await bot.send_message(
                context.request.user_chat_id,
                message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except TelegramBadRequest:
            # Telegram may reject Web App buttons until the customer configures
            # the domain for their bot. Keep activation possible through the
            # bot-native deep link instead of dropping the join request.
            await bot.send_message(
                context.request.user_chat_id,
                message,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Продолжить и вступить", url=fallback_url)]
                    ]
                ),
            )


async def probe_persistent_write(context: ActivationContext, keyring: TokenKeyring) -> None:
    bot_token = keyring.decrypt(context.bot.token_ciphertext)
    async with bot_client(bot_token, bot_factory=Bot) as bot:
        await bot.send_chat_action(context.contact.telegram_id, "typing")


async def send_client_notice(context: ClientMessageContext, keyring: TokenKeyring) -> None:
    token = keyring.decrypt(context.bot.token_ciphertext)
    payload = context.message.payload
    kind = str(payload.get("kind") or "")
    markup = None
    if kind == "captcha":
        challenge = str(payload.get("challenge") or "")
        answer = str(payload.get("answer") or "")
        options = [str(item) for item in payload.get("options", [])]
        text = (
            "<b>Подписка на сообщения</b>\n\n"
            "После подтверждения этот бот сможет присылать вам информационные и рекламные "
            "сообщения. Частоту и содержание определяет владелец бота. Отписаться можно в "
            "любой момент командой /stop или кнопкой в рассылке.\n\n"
            f"Чтобы подтвердить согласие, выберите символ <b>{answer}</b>."
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=item,
                        callback_data=captcha_callback(challenge, item),
                    )
                    for item in options
                ]
            ]
        )
    else:
        text = client_message_text(payload)
    if not text:
        raise ValueError("Unsupported client notice")
    async with bot_client(token, bot_factory=Bot) as bot:
        await bot.send_message(
            context.contact.telegram_id,
            text,
            parse_mode="HTML" if kind == "captcha" else None,
            reply_markup=markup,
        )


def _entities(raw: list[dict[str, Any]] | None) -> list[MessageEntity] | None:
    return [MessageEntity.model_validate(entity) for entity in raw] if raw else None


@dataclass
class _OperationMedia:
    storage_key: str
    original_name: str
    size: int


@dataclass(frozen=True)
class PremiumEmojiDeliveryResult:
    requested: int = 0
    preserved: bool = True
    message_ids: tuple[int, ...] = ()

    @property
    def downgraded(self) -> bool:
        return self.requested > 0 and not self.preserved


_premium_emoji_capability_cache: dict[str, bool] = {}


def _custom_emoji_ids(entities: Any) -> list[str]:
    if not isinstance(entities, list):
        return []
    return [
        str(entity.get("custom_emoji_id"))
        for entity in entities
        if isinstance(entity, dict)
        and entity.get("type") == "custom_emoji"
        and entity.get("custom_emoji_id")
    ]


def _sent_custom_emoji_ids(messages: list[Message]) -> list[str]:
    result: list[str] = []
    for message in messages:
        for entity in [*(message.entities or []), *(message.caption_entities or [])]:
            if str(entity.type) == "custom_emoji" and entity.custom_emoji_id:
                result.append(entity.custom_emoji_id)
    return result


def observe_custom_emoji_delivery(
    bot: Bot, requested_ids: list[str], messages: list[Message]
) -> PremiumEmojiDeliveryResult:
    message_ids = tuple(
        message_id
        for message in messages
        if isinstance(message_id := getattr(message, "message_id", None), int)
    )
    if not requested_ids:
        return PremiumEmojiDeliveryResult(message_ids=message_ids)
    preserved = Counter(requested_ids) <= Counter(_sent_custom_emoji_ids(messages))
    if preserved:
        _premium_emoji_capability_cache[_bot_fingerprint(bot)] = True
        CUSTOM_EMOJI_PRESERVED.inc()
    else:
        # Only cache a positively confirmed capability. A downgrade may be
        # transient (or caused by Telegram configuration changing), so the next
        # delivery must verify the actual response again.
        _premium_emoji_capability_cache.pop(_bot_fingerprint(bot), None)
        CUSTOM_EMOJI_DOWNGRADED.inc()
    return PremiumEmojiDeliveryResult(len(requested_ids), preserved, message_ids)


_NATIVE_STICKER_CACHE_TTL = 3600.0
_native_sticker_cache: dict[tuple[str, str], tuple[float, str]] = {}


def _bot_fingerprint(bot: Bot) -> str:
    return hashlib.sha256(bot.token.encode()).hexdigest()[:20]


def _native_sticker_payload(raw_media: dict[str, Any]) -> dict[str, Any] | None:
    payload = raw_media.get("payload")
    if not isinstance(payload, dict):
        return None
    native = payload.get("telegram_native")
    return native if isinstance(native, dict) else None


async def resolve_native_sticker(bot: Bot, raw_media: dict[str, Any]) -> str | None:
    """Resolve a sticker to a file_id scoped to the bot performing the delivery."""

    native = _native_sticker_payload(raw_media)
    if native is None:
        PREMIUM_MEDIA.labels("legacy", "fallback").inc()
        return None
    custom_emoji_id = str(native.get("custom_emoji_id") or "")
    set_name = str(native.get("set_name") or "")
    file_unique_id = str(native.get("file_unique_id") or "")
    identity = f"emoji:{custom_emoji_id}" if custom_emoji_id else f"set:{set_name}:{file_unique_id}"
    if not custom_emoji_id and (not set_name or not file_unique_id):
        PREMIUM_MEDIA.labels("native", "incomplete").inc()
        return None
    cache_key = (_bot_fingerprint(bot), identity)
    cached = _native_sticker_cache.get(cache_key)
    if cached is not None and cached[0] > monotonic():
        PREMIUM_MEDIA.labels("native", "cache_hit").inc()
        return cached[1]
    try:
        if custom_emoji_id:
            stickers = await bot.get_custom_emoji_stickers([custom_emoji_id])
            candidate = next(
                (item for item in stickers if item.custom_emoji_id == custom_emoji_id), None
            )
        else:
            sticker_set = await bot.get_sticker_set(set_name)
            candidate = next(
                (item for item in sticker_set.stickers if item.file_unique_id == file_unique_id), None
            )
    except TelegramAPIError:
        PREMIUM_MEDIA.labels("native", "telegram_error").inc()
        return None
    if candidate is None:
        PREMIUM_MEDIA.labels("native", "not_found").inc()
        return None
    _native_sticker_cache[cache_key] = (
        monotonic() + _NATIVE_STICKER_CACHE_TTL,
        candidate.file_id,
    )
    PREMIUM_MEDIA.labels("custom_emoji" if custom_emoji_id else "premium_sticker", "resolved").inc()
    return candidate.file_id


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
    payload = bind_subscribe_links(
        context.operation.payload,
        bot_username=context.bot.username,
        path_secret=context.bot.path_secret,
        channel_id=context.channel.id,
    )
    async with bot_client(token, bot_factory=Bot) as bot:
        await send_compiled_operation(
            bot,
            context.target_chat_id,
            context.operation.operation_type,
            payload,
            context.operation.media,
            storage,
            first_name=context.contact.first_name,
        )


def bind_subscribe_links(
    payload: dict[str, Any], *, bot_username: str, path_secret: str, channel_id: int
) -> dict[str, Any]:
    """Bind portable subscribe buttons to the actual delivery channel."""
    result = deepcopy(payload)
    keyboard = result.get("keyboard")
    if not isinstance(keyboard, dict):
        return result
    for row in keyboard.get("rows") or []:
        if not isinstance(row, list):
            continue
        for button in row:
            if not isinstance(button, dict):
                continue
            value = str(button.get("value") or "")
            marker = "?start="
            if marker not in value:
                continue
            subscribe_token = value.split(marker, 1)[1]
            verified = verify_subscribe_token(subscribe_token, path_secret)
            if verified is None or verified[1] != 0:
                continue
            button["value"] = (
                f"https://t.me/{bot_username}?start="
                f"{build_subscribe_token(verified[0], channel_id, path_secret)}"
            )
    return result


async def send_compiled_operation(
    bot: Bot,
    chat_id: int,
    operation_type: str,
    payload: dict[str, Any],
    raw_media: list[dict[str, Any]],
    storage: ObjectStorage,
    *,
    first_name: str | None = None,
    on_sent: Callable[[tuple[int, ...]], Awaitable[None]] | None = None,
) -> PremiumEmojiDeliveryResult:
    """Send one compiled operation with either a client bot or the preview bot."""
    async def finish_send(requested: list[str], messages: list[Message]) -> PremiumEmojiDeliveryResult:
        if on_sent is not None:
            # Register messages before media cleanup can fail, so preview UI
            # still owns every message actually accepted by Telegram.
            await on_sent(tuple(message.message_id for message in messages))
        return observe_custom_emoji_delivery(bot, requested, messages)

    if first_name is not None:
        payload, raw_media = personalize_operation(payload, raw_media, first_name)
    markup = _reply_markup(payload.get("keyboard") if isinstance(payload.get("keyboard"), dict) else None)
    if operation_type == "sticker" and raw_media:
        native_file_id = await resolve_native_sticker(bot, raw_media[0])
        if native_file_id:
            sent_sticker = await bot.send_sticker(chat_id, native_file_id, reply_markup=markup)
            return await finish_send([], [sent_sticker])
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
                    "parse_mode": None,
                }
                if media_type in {"photo", "video"}:
                    album_kwargs["has_spoiler"] = bool(item_payload.get("has_spoiler"))
                album.append(media_class(**album_kwargs))
            if len(album) < 2:
                raise ValueError("Telegram media groups require at least two items")
            requested = [
                emoji_id
                for item in raw_media
                for emoji_id in _custom_emoji_ids(
                    item.get("payload", {}).get("caption_entities")
                    if isinstance(item.get("payload"), dict)
                    else None
                )
            ]
            try:
                sent_album = await bot.send_media_group(chat_id, media=album)
            except TelegramAPIError:
                if requested:
                    CUSTOM_EMOJI_TELEGRAM_ERROR.inc()
                raise
            return await finish_send(requested, sent_album)
        kind = operation_type
        if kind == "text":
            requested = _custom_emoji_ids(payload.get("entities"))
            try:
                sent_message = await bot.send_message(
                    chat_id,
                    str(payload.get("text") or ""),
                    entities=_entities(payload.get("entities")),
                    parse_mode=None,
                    reply_markup=markup,
                    link_preview_options=LinkPreviewOptions(
                        is_disabled=not bool(payload.get("link_preview_enabled", True))
                    ),
                )
            except TelegramAPIError:
                if requested:
                    CUSTOM_EMOJI_TELEGRAM_ERROR.inc()
                raise
            return await finish_send(requested, [sent_message])
        if uploads:
            upload = uploads[0]
            item = raw_media[0]
            raw_item_payload = item.get("payload")
            item_payload = raw_item_payload if isinstance(raw_item_payload, dict) else {}
            media_kwargs: dict[str, Any] = {
                "caption": item_payload.get("caption"),
                "caption_entities": _entities(item_payload.get("caption_entities")),
                "parse_mode": None,
                "reply_markup": markup,
            }
            # None disables an owner bot's default HTML parser. Dropping it
            # lets aiogram replace explicit caption entities with parsed HTML.
            media_kwargs = {
                key: value for key, value in media_kwargs.items()
                if value is not None or key == "parse_mode"
            }
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
                requested = _custom_emoji_ids(item_payload.get("caption_entities"))
                try:
                    sent_media = await sender(
                        chat_id,
                        upload,
                        **(
                            {"reply_markup": markup}
                            if kind in {"video_note", "sticker"}
                            else media_kwargs
                        ),
                    )
                except TelegramAPIError:
                    if requested:
                        CUSTOM_EMOJI_TELEGRAM_ERROR.inc()
                    raise
                return await finish_send(requested, [cast(Message, sent_media)])
        if kind == "location":
            location = payload["location"]
            sent_message = await bot.send_location(
                chat_id,
                location["latitude"],
                location["longitude"],
                reply_markup=markup,
            )
        elif kind == "contact":
            contact = payload["contact"]
            sent_message = await bot.send_contact(
                chat_id,
                contact["phone_number"],
                contact["first_name"],
                last_name=contact.get("last_name"),
                reply_markup=markup,
            )
        elif kind == "venue":
            venue = payload["venue"]
            location = venue["location"]
            sent_message = await bot.send_venue(
                chat_id,
                location["latitude"],
                location["longitude"],
                venue["title"],
                venue["address"],
                reply_markup=markup,
            )
        elif kind == "dice":
            sent_message = await bot.send_dice(
                chat_id,
                emoji=payload["dice"].get("emoji"),
                reply_markup=markup,
            )
        elif kind == "poll":
            poll = payload["poll"]
            sent_message = await bot.send_poll(
                chat_id,
                poll["question"],
                [option["text"] for option in poll.get("options", [])],
                is_anonymous=poll.get("is_anonymous", True),
                reply_markup=markup,
            )
        else:
            raise ValueError("Unsupported compiled Telegram operation")
        return await finish_send([], [sent_message])


async def send_greeting(context: DeliveryContext, storage: ObjectStorage, keyring: TokenKeyring) -> None:
    token = keyring.decrypt(context.bot.token_ciphertext)
    legacy_media = [
        {
            "storage_key": media.storage_key,
            "original_name": media.original_name or "media.bin",
            "size": media.size,
            "media_type": media.media_type,
            "payload": {},
        }
        for media in context.media
    ]
    payload, _ = personalize_operation(
        context.version.payload,
        legacy_media,
        context.contact.first_name,
    )
    kind = str(payload.get("type") or "text")
    async with AsyncExitStack() as stack:
        paths = [await stack.enter_async_context(storage.materialize(media)) for media in context.media]
        uploads = [
            FSInputFile(path, filename=media.original_name or "media.bin")
            for path, media in zip(paths, context.media, strict=True)
        ]
        async with bot_client(token, bot_factory=Bot) as bot:
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
                        "parse_mode": None,
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
                    parse_mode=None,
                )
                return
            if uploads:
                upload = uploads[0]
                kwargs = {
                    "caption": payload.get("caption"),
                    "caption_entities": _entities(payload.get("caption_entities")),
                    "parse_mode": None,
                }
                kwargs = {
                    key: value for key, value in kwargs.items()
                    if value is not None or key == "parse_mode"
                }
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
    async with bot_client(token, bot_factory=Bot) as bot:
        await bot.approve_chat_join_request(context.channel.telegram_id, context.contact.telegram_id)
