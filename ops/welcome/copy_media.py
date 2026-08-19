#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

MEDIA_KEYS_QUERY = """
    SELECT storage_key FROM welcome_bots_welcomemedia
    UNION
    SELECT storage_key FROM welcome_bots_welcomedraftmedia
    ORDER BY storage_key
"""


@dataclass(frozen=True)
class S3Settings:
    endpoint: str
    region: str
    access_key: str
    secret_key: str
    bucket: str


def client(settings: S3Settings):
    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint,
        region_name=settings.region,
        aws_access_key_id=settings.access_key,
        aws_secret_access_key=settings.secret_key,
        config=Config(s3={"addressing_style": "path"}),
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def copy_one(source, destination, source_bucket: str, destination_bucket: str, key: str) -> str:
    descriptor, raw_path = tempfile.mkstemp(prefix="gramly-media-")
    os.close(descriptor)
    path = Path(raw_path)
    try:
        source.download_file(source_bucket, key, str(path))
        checksum = sha256(path)
        size = path.stat().st_size
        try:
            existing = destination.head_object(Bucket=destination_bucket, Key=key)
            if (
                existing.get("Metadata", {}).get("gramly-sha256") == checksum
                and int(existing.get("ContentLength") or -1) == size
            ):
                return "skipped"
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                raise
        destination.upload_file(
            str(path),
            destination_bucket,
            key,
            ExtraArgs={"Metadata": {"gramly-sha256": checksum}},
        )
        uploaded = destination.head_object(Bucket=destination_bucket, Key=key)
        if uploaded.get("Metadata", {}).get("gramly-sha256") != checksum:
            raise RuntimeError("Destination checksum metadata mismatch")
        if int(uploaded.get("ContentLength") or -1) != size:
            raise RuntimeError("Destination object size mismatch")
        return "copied"
    finally:
        path.unlink(missing_ok=True)


async def run(args: argparse.Namespace) -> None:
    connection = await asyncpg.connect(args.database_url)
    try:
        rows = await connection.fetch(MEDIA_KEYS_QUERY)
    finally:
        await connection.close()
    keys = [str(row[0]) for row in rows]
    if not args.execute:
        print(f"dry-run objects={len(keys)}")
        return
    source = client(args.source)
    destination = client(args.destination)
    semaphore = asyncio.Semaphore(args.concurrency)

    async def bounded(key: str) -> str:
        async with semaphore:
            return await asyncio.to_thread(
                copy_one,
                source,
                destination,
                args.source.bucket,
                args.destination.bucket,
                key,
            )

    results = await asyncio.gather(*(bounded(key) for key in keys))
    print(
        f"objects={len(keys)} copied={results.count('copied')} "
        f"skipped={results.count('skipped')}"
    )


def s3_settings(prefix: str) -> S3Settings:
    def required(name: str) -> str:
        value = os.environ.get(f"{prefix}_{name}", "")
        if not value:
            raise ValueError(f"Missing {prefix}_{name}")
        return value

    return S3Settings(
        endpoint=required("ENDPOINT_URL"),
        region=os.environ.get(f"{prefix}_REGION", "auto"),
        access_key=required("ACCESS_KEY_ID"),
        secret_key=required("SECRET_ACCESS_KEY"),
        bucket=required("BUCKET_NAME"),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Checksum-verified Welcome media copy")
    parser.add_argument("--database-url", default=os.environ.get("SOURCE_DATABASE_URL", ""))
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("SOURCE_DATABASE_URL is required")
    try:
        args.source = s3_settings("SOURCE_S3")
        args.destination = s3_settings("DESTINATION_S3")
    except ValueError as exc:
        parser.error(str(exc))
    return args


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
