from __future__ import annotations

import asyncio
import io
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePath
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .config import Settings


class StoredMedia(Protocol):
    storage_key: str
    original_name: str
    size: int


class MediaTooLargeError(ValueError):
    pass


class ObjectStorageError(RuntimeError):
    pass


class ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket_name
        self.max_bytes = settings.media_max_bytes
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region_name,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            config=Config(s3={"addressing_style": settings.s3_addressing_style}),
        )

    def _download(self, media: StoredMedia, target: Path) -> None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=media.storage_key)
            declared = int(response.get("ContentLength") or media.size or 0)
            if declared > self.max_bytes:
                response["Body"].close()
                raise MediaTooLargeError("Stored media exceeds the configured limit")
            total = 0
            try:
                with target.open("wb") as handle:
                    for chunk in response["Body"].iter_chunks(chunk_size=256 * 1024):
                        total += len(chunk)
                        if total > self.max_bytes:
                            raise MediaTooLargeError("Stored media exceeds the configured limit")
                        handle.write(chunk)
            finally:
                response["Body"].close()
        except MediaTooLargeError:
            raise
        except (BotoCoreError, ClientError, OSError) as exc:
            raise ObjectStorageError("Object Storage request failed") from exc

    def _upload(self, key: str, body: bytes, content_type: str) -> None:
        if len(body) > self.max_bytes:
            raise MediaTooLargeError("Media exceeds the configured limit")
        try:
            self.client.upload_fileobj(
                io.BytesIO(body),
                self.bucket,
                key,
                ExtraArgs={"ContentType": content_type or "application/octet-stream"},
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            raise ObjectStorageError("Object Storage upload failed") from exc

    async def upload(self, key: str, body: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._upload, key, body, content_type)

    def _delete_many(self, keys: list[str]) -> None:
        if not keys:
            return
        try:
            for offset in range(0, len(keys), 1000):
                response = self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={
                        "Objects": [{"Key": key} for key in keys[offset : offset + 1000]],
                        "Quiet": True,
                    },
                )
                if response.get("Errors"):
                    raise ObjectStorageError("Object Storage reported delete failures")
        except ObjectStorageError:
            raise
        except (BotoCoreError, ClientError, OSError) as exc:
            raise ObjectStorageError("Object Storage delete failed") from exc

    async def delete_many(self, keys: list[str]) -> None:
        await asyncio.to_thread(self._delete_many, keys)

    @asynccontextmanager
    async def materialize(self, media: StoredMedia) -> AsyncIterator[Path]:
        suffix = PurePath(media.original_name or "media.bin").suffix[:16]
        descriptor, raw_path = tempfile.mkstemp(prefix="welcome-", suffix=suffix, dir="/tmp")
        os.close(descriptor)
        path = Path(raw_path)
        try:
            await asyncio.to_thread(self._download, media, path)
            yield path
        finally:
            path.unlink(missing_ok=True)
