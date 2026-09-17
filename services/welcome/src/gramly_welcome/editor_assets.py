"""Authenticated editor assets and Telegram emoji sets; no bot URLs leave the API."""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import re
import time
import uuid
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

from aiogram import Bot
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from .admin_api import AdminDep
from .app_api import CsrfUserDep, CurrentUserDep, SessionDep, SettingsDep
from .catalog_media import store_media
from .models import ContentAttachment, ContentFlow, ContentFlowVersion, ContentStep, EditorMedia, ManagedBot
from .storage import ObjectStorage

router = APIRouter(prefix="/api/v1/editor", tags=["editor-assets"])
admin_router = APIRouter(prefix="/api/admin/v1/editor", tags=["editor-assets"])


@router.post("/media", status_code=201)
async def upload_owner_media(
    request: Request, user: CsrfUserDep, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    media_id = uuid.uuid4()
    descriptor = await store_media(request, settings, prefix=f"editor/{user.auth.owner.id}/{media_id.hex}")
    session.add(EditorMedia(id=media_id, owner_id=user.auth.owner.id, descriptor=descriptor))
    await session.commit()
    return {**descriptor, "media_id": str(media_id), "preview_url": f"/api/v1/editor/media/{media_id}"}


async def stream_asset(descriptor: Any, settings: Any) -> StreamingResponse:
    storage = ObjectStorage(settings)
    try:
        obj = await asyncio.to_thread(
            storage.client.get_object, Bucket=storage.bucket, Key=descriptor["storage_key"]
        )
    except Exception:
        raise HTTPException(404, "Файл недоступен") from None

    async def chunks() -> Any:
        try:
            while part := await asyncio.to_thread(obj["Body"].read, 256 * 1024):
                yield part
        finally:
            obj["Body"].close()

    return StreamingResponse(
        chunks(),
        media_type=descriptor.get("mime_type") or "application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment" if descriptor.get("media_type") == "document" else "inline",
        },
    )


@router.get("/media/{media_id}")
async def owner_asset(
    media_id: uuid.UUID, user: CurrentUserDep, session: SessionDep, settings: SettingsDep
) -> StreamingResponse:
    asset = await session.get(EditorMedia, media_id)
    if asset is None or asset.owner_id != user.auth.owner.id:
        raise HTTPException(404, "Файл недоступен")
    return await stream_asset(asset.descriptor, settings)


@router.get("/attachments/{attachment_id}")
async def owner_attachment(
    attachment_id: int, user: CurrentUserDep, session: SessionDep, settings: SettingsDep
) -> StreamingResponse:
    attachment = await session.scalar(
        select(ContentAttachment)
        .join(ContentStep)
        .join(ContentFlowVersion)
        .join(ContentFlow)
        .join(ManagedBot)
        .where(ContentAttachment.id == attachment_id, ManagedBot.owner_id == user.auth.owner.id)
    )
    if attachment is None:
        raise HTTPException(404, "Файл недоступен")
    return await stream_asset(
        {k: getattr(attachment, k) for k in ("storage_key", "mime_type", "media_type")}, settings
    )


def emoji_set_name(link: str) -> str:
    parsed = urlsplit(link.strip())
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() not in {"t.me", "telegram.me"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Вставьте ссылку https://t.me/addemoji/НазваниеНабора")
    match = re.fullmatch(r"/addemoji/([A-Za-z][A-Za-z0-9_]{0,63})/?", parsed.path)
    if not match:
        raise ValueError("Нужна ссылка на набор эмодзи, а не стикеров")
    return match[1]


_set_cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_asset_cache: OrderedDict[tuple[str, bool], tuple[float, bytes, str]] = OrderedDict()
_emoji_slots = asyncio.Semaphore(4)


async def emoji_set(link: str, settings: Any) -> dict[str, Any]:
    try:
        name = emoji_set_name(link)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    cached = _set_cache.get(name)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    try:
        async with _emoji_slots, Bot(settings.interface_bot_token) as bot:
            pack = await asyncio.wait_for(bot.get_sticker_set(name), 15)
        if pack.sticker_type != "custom_emoji":
            raise HTTPException(422, "Это набор стикеров. Используйте ссылку на набор эмодзи")
        result = {
            "name": pack.name,
            "title": pack.title,
            "items": [
                {
                    "id": s.custom_emoji_id,
                    "emoji": s.emoji or "⭐",
                    "format": "tgs" if s.is_animated else "webm" if s.is_video else "webp",
                }
                for s in pack.stickers
                if s.custom_emoji_id
            ],
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Telegram не вернул набор. Проверьте ссылку и попробуйте ещё раз") from None
    _set_cache[name] = (time.monotonic() + 3600, result)
    while len(_set_cache) > 32:
        _set_cache.popitem(last=False)
    return result


async def emoji_asset(emoji_id: str, thumbnail: bool, settings: Any) -> Response:
    if not re.fullmatch(r"[0-9]{1,30}", emoji_id):
        raise HTTPException(404, "Эмодзи недоступен")
    cached = _asset_cache.get((emoji_id, thumbnail))
    if cached and cached[0] > time.monotonic():
        return Response(
            cached[1],
            media_type=cached[2],
            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )
    try:
        async with _emoji_slots, Bot(settings.interface_bot_token) as bot:
            async with asyncio.timeout(20):
                stickers = await bot.get_custom_emoji_stickers([emoji_id])
                if not stickers:
                    raise ValueError("Missing emoji")
                sticker = stickers[0]
                file_id = sticker.thumbnail.file_id if thumbnail and sticker.thumbnail else sticker.file_id
                file = await bot.get_file(file_id)
                if not file.file_path or (file.file_size or 0) > 512 * 1024:
                    raise ValueError("Emoji file too large")
                output = io.BytesIO()
                await bot.download_file(file.file_path, destination=output)
                data = output.getvalue()
                if len(data) > 512 * 1024:
                    raise ValueError("Emoji file too large")
        mime = "image/webp"
        if data.startswith(b"\x1f\x8b"):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                data = compressed.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError("Animation too large")
            animation = json.loads(data)
            # Telegram animations should be self-contained; reject external resources.
            if any(a.get("p") or a.get("u") for a in animation.get("assets", [])):
                raise ValueError("External animation assets")
            mime = "application/json"
        elif data.startswith(b"\x1aE\xdf\xa3"):
            mime = "video/webm"
        elif data.startswith(b"\xff\xd8"):
            mime = "image/jpeg"
        elif data.startswith(b"\x89PNG"):
            mime = "image/png"
        elif not data.startswith(b"RIFF"):
            raise ValueError("Unknown emoji format")
    except Exception:
        raise HTTPException(502, "Превью эмодзи временно недоступно") from None
    _asset_cache[emoji_id, thumbnail] = (time.monotonic() + 3600, data, mime)
    while len(_asset_cache) > 64:
        _asset_cache.popitem(last=False)
    return Response(
        data,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/emoji-sets")
async def owner_emoji_set(
    user: CurrentUserDep, settings: SettingsDep, link: str = Query(max_length=200)
) -> dict[str, Any]:
    return await emoji_set(link, settings)


@admin_router.get("/emoji-sets")
async def admin_emoji_set(
    admin: AdminDep, settings: SettingsDep, link: str = Query(max_length=200)
) -> dict[str, Any]:
    return await emoji_set(link, settings)


@router.get("/emoji/{emoji_id}")
async def owner_emoji_asset(
    emoji_id: str, user: CurrentUserDep, settings: SettingsDep, thumbnail: bool = False
) -> Response:
    return await emoji_asset(emoji_id, thumbnail, settings)


@admin_router.get("/emoji/{emoji_id}")
async def admin_emoji_asset(
    emoji_id: str, admin: AdminDep, settings: SettingsDep, thumbnail: bool = False
) -> Response:
    return await emoji_asset(emoji_id, thumbnail, settings)
