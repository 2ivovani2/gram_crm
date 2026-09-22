from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import case, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DeliveryJob

LANES = ("urgent", "interactive", "standard", "bulk")


def stream_name(prefix: str, lane: str) -> str:
    if lane not in LANES:
        raise ValueError("Unknown delivery lane")
    return f"{prefix}:{lane}"


async def ensure_consumer_groups(redis: Redis, prefix: str) -> None:
    for lane in LANES:
        try:
            await redis.xgroup_create(
                stream_name(prefix, lane), f"delivery-{lane}", id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise


async def recover_unsignaled_jobs(session: AsyncSession, *, stale_seconds: int = 30) -> int:
    result = cast(CursorResult[Any], await session.execute(
        update(DeliveryJob)
        .where(
            DeliveryJob.status == "signaled",
            DeliveryJob.signaled_at < datetime.now(UTC) - timedelta(seconds=stale_seconds),
        )
        .values(status="pending", signaled_at=None, stream_id=None)
    ))
    await session.commit()
    return int(result.rowcount or 0)


async def claim_jobs_for_signal(session: AsyncSession, *, limit: int) -> list[DeliveryJob]:
    now = datetime.now(UTC)
    lane_order = case(
        (DeliveryJob.lane == "urgent", 0),
        (DeliveryJob.lane == "interactive", 1),
        (DeliveryJob.lane == "standard", 2),
        else_=3,
    )
    jobs = list(
        await session.scalars(
            select(DeliveryJob)
            .where(
                DeliveryJob.status.in_(("pending", "retry")),
                DeliveryJob.next_attempt_at <= now,
            )
            .order_by(
                lane_order,
                DeliveryJob.deadline_at.asc().nulls_last(),
                DeliveryJob.next_attempt_at,
                DeliveryJob.bot_id.asc().nulls_last(),
                DeliveryJob.id,
            )
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    )
    for job in jobs:
        job.status = "signaled"
        job.signaled_at = now
    await session.commit()
    return jobs


async def publish_jobs(redis: Redis, session: AsyncSession, jobs: list[DeliveryJob], *, prefix: str) -> int:
    published: list[tuple[int, str]] = []
    try:
        for job in jobs:
            message_id = await redis.xadd(
                stream_name(prefix, job.lane),
                {"job_id": str(job.id), "source_kind": job.source_kind},
                maxlen=100_000,
                approximate=True,
            )
            if isinstance(message_id, bytes):
                message_id = message_id.decode("ascii")
            published.append((job.id, str(message_id)))
    except Exception:
        await session.execute(
            update(DeliveryJob)
            .where(DeliveryJob.id.in_([job.id for job in jobs]), DeliveryJob.status == "signaled")
            .values(status="pending", signaled_at=None, stream_id=None)
        )
        await session.commit()
        raise
    for job_id, message_id in published:
        await session.execute(
            update(DeliveryJob)
            .where(DeliveryJob.id == job_id, DeliveryJob.status == "signaled")
            .values(stream_id=message_id)
        )
    await session.commit()
    return len(published)


def decode_stream_fields(fields: dict[Any, Any]) -> tuple[int, str]:
    normalized = {
        (key.decode() if isinstance(key, bytes) else str(key)):
        (value.decode() if isinstance(value, bytes) else str(value))
        for key, value in fields.items()
    }
    return int(normalized["job_id"]), normalized["source_kind"]
