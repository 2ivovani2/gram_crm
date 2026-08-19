from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import IdempotencyRecord


class IdempotencyConflictError(ValueError):
    """The same idempotency key was reused for a different request."""


@dataclass(frozen=True)
class StoredResponse:
    status: int
    body: dict[str, Any]


def request_digest(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def claim_request(
    session: AsyncSession,
    *,
    owner_id: int,
    key: str,
    payload: object,
    lifetime_seconds: int = 86_400,
) -> StoredResponse | None:
    if not key or len(key) > 128:
        raise ValueError("Idempotency key must contain 1 to 128 characters")
    digest = request_digest(payload)
    inserted_id = await session.scalar(
        insert(IdempotencyRecord)
        .values(
            owner_id=owner_id,
            key=key,
            request_hash=digest,
            expires_at=datetime.now(UTC) + timedelta(seconds=lifetime_seconds),
        )
        .on_conflict_do_nothing(constraint="uq_idempotency_owner_key")
        .returning(IdempotencyRecord.id)
    )
    if inserted_id is not None:
        await session.flush()
        return None

    existing = await session.scalar(
        select(IdempotencyRecord)
        .where(IdempotencyRecord.owner_id == owner_id, IdempotencyRecord.key == key)
        .with_for_update()
    )
    if existing is None:
        raise RuntimeError("Idempotency claim disappeared")
    if existing.request_hash != digest:
        raise IdempotencyConflictError("Idempotency key was already used")
    if existing.response_status is None or existing.response_body is None:
        raise IdempotencyConflictError("Identical request is already being processed")
    return StoredResponse(existing.response_status, existing.response_body)


async def store_response(
    session: AsyncSession,
    *,
    owner_id: int,
    key: str,
    status: int,
    body: dict[str, Any],
) -> None:
    record = await session.scalar(
        select(IdempotencyRecord)
        .where(IdempotencyRecord.owner_id == owner_id, IdempotencyRecord.key == key)
        .with_for_update()
    )
    if record is None:
        raise RuntimeError("Idempotency record does not exist")
    record.response_status = status
    record.response_body = body
    await session.commit()
