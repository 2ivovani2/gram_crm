"""Bounded, authenticated catalog media uploads; shared blobs are immutable."""
import asyncio
import hashlib
import re
import tempfile
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .admin_api import AdminDep, MutationAdminDep, SettingsDep
from .storage import ObjectStorage

router = APIRouter(prefix="/api/admin/v1/catalog-media", tags=["welcome-admin-catalog"])
MAX_BYTES = 20 * 1024 * 1024


def identify(data: bytes) -> tuple[str, str, set[str]]:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg", {"photo"}
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png", {"photo"}
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif", "image/gif", {"animation"}
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp", "image/webp", {"photo", "sticker"}
    if data[4:8] == b"ftyp":
        return "mp4", "video/mp4", {"video", "video_note", "animation"}
    if data.startswith(b"OggS"):
        return "ogg", "audio/ogg", {"voice", "audio"}
    if data.startswith(b"ID3") or (len(data) > 1 and data[0] == 255 and data[1] & 224 == 224):
        return "mp3", "audio/mpeg", {"audio"}
    if data.startswith(b"%PDF-"):
        return "pdf", "application/pdf", {"document"}
    if data.startswith(b"PK\x03\x04"):
        return "zip", "application/zip", {"document"}
    raise HTTPException(422, "Формат не поддерживается. Используйте JPG, PNG, WEBP, GIF, MP4, MP3, OGG, PDF или ZIP.")


@router.post("")
async def upload_media(request: Request, _admin: MutationAdminDep, settings: SettingsDep,
                       media_type: str = "auto") -> dict[str, Any]:
    limit = min(MAX_BYTES, settings.media_max_bytes)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "Файл больше 20 МБ")
    size = 0
    digest = hashlib.sha256()
    with tempfile.TemporaryFile() as handle:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "Файл больше 20 МБ")
            digest.update(chunk)
            await asyncio.to_thread(handle.write, chunk)
        handle.seek(0)
        ext, mime, kinds = identify(handle.read(32))
        default = {"jpg":"photo", "png":"photo", "webp":"sticker", "gif":"animation",
                   "mp4":"video", "ogg":"voice", "mp3":"audio", "pdf":"document", "zip":"document"}[ext]
        selected = default if media_type == "auto" else media_type
        if selected not in kinds:
            raise HTTPException(422, "Тип сообщения не соответствует формату файла")
        key = f"catalog/uploads/{digest.hexdigest()}.{ext}"
        handle.seek(0)
        storage = ObjectStorage(settings)
        try:
            await asyncio.to_thread(storage.client.upload_fileobj, handle, storage.bucket, key,
                                    ExtraArgs={"ContentType": mime})
        except Exception:
            raise HTTPException(503, "Хранилище временно недоступно. Повторите загрузку.") from None
    return {"media_type": selected, "storage_key": key, "original_name": f"media.{ext}",
            "mime_type": mime, "size": size, "payload": {}}


@router.get("/{filename}")
async def preview_media(filename: str, _admin: AdminDep, settings: SettingsDep) -> StreamingResponse:
    if not re.fullmatch(r"[a-f0-9]{64}\.(jpg|png|webp|gif|mp4|ogg|mp3|pdf|zip)", filename):
        raise HTTPException(404, "Файл не найден")
    storage = ObjectStorage(settings)
    try:
        obj = await asyncio.to_thread(storage.client.get_object, Bucket=storage.bucket, Key="catalog/uploads/" + filename)
    except Exception:
        raise HTTPException(404, "Файл недоступен") from None
    async def chunks() -> AsyncIterator[bytes]:
        try:
            while chunk := await asyncio.to_thread(obj["Body"].read, 256 * 1024):
                yield chunk
        finally:
            obj["Body"].close()
    mime = obj.get("ContentType", "application/octet-stream")
    return StreamingResponse(chunks(), media_type=mime, headers={"Cache-Control":"no-store",
        "X-Content-Type-Options":"nosniff", "Content-Disposition":
        "attachment" if filename.endswith((".pdf", ".zip")) else "inline"})
