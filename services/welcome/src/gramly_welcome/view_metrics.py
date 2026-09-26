"""Measured channel windows. No interpolation or historical reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ViewCollectionRun, ViewPostObservation


@dataclass(frozen=True)
class ViewWindows:
    views_24h: int | None
    views_48h: int | None
    views_72h: int | None
    er24_percent: float | None
    observed_at: datetime | None


async def measured_windows(
    session: AsyncSession, channel_id: int, subscribers: int, *, now: datetime | None = None
) -> ViewWindows:
    current = now or datetime.now(UTC)
    run = await session.scalar(
        select(ViewCollectionRun)
        .where(ViewCollectionRun.channel_id == channel_id)
        .order_by(ViewCollectionRun.observed_at.desc(), ViewCollectionRun.id.desc())
        .limit(1)
    )
    if (
        run is None
        or not run.complete
        or run.error_code
        or run.observed_at < current - timedelta(hours=2)
        or run.coverage_start_at > current - timedelta(hours=72)
    ):
        return ViewWindows(None, None, None, None, run.observed_at if run else None)
    # Only use posts from this complete scan. Previously seen but later
    # deleted/inaccessible posts cannot silently leak into a new window.
    latest = select(ViewPostObservation).where(
        ViewPostObservation.channel_id == channel_id,
        ViewPostObservation.observed_at == run.observed_at,
        ViewPostObservation.published_at >= current - timedelta(hours=72),
    )
    posts = (await session.scalars(latest)).all()
    windows: dict[int, tuple[int, int]] = {}
    for hours in (24, 48, 72):
        selected = [post for post in posts if post.published_at >= current - timedelta(hours=hours)]
        windows[hours] = (sum(post.views for post in selected), len(selected))
    views24, posts24 = windows[24]
    er24 = round(100 * views24 / posts24 / subscribers, 2) if subscribers > 0 and posts24 else None
    return ViewWindows(views24, windows[48][0], windows[72][0], er24, run.observed_at)
