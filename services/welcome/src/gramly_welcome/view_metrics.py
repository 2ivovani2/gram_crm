"""Measured channel windows. No interpolation or historical reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ViewCollectionRun, ViewPostObservation


@dataclass(frozen=True)
class ViewWindows:
    views_24h: int | None
    views_48h: int | None
    views_72h: int | None
    er24_percent: float | None
    observed_at: datetime | None
    posts_24h: int = 0
    posts_48h: int = 0
    posts_72h: int = 0


WINDOW_HOURS = (24, 48, 72)
SAMPLE_GRACE = timedelta(hours=2)


async def measured_windows(
    session: AsyncSession, channel_id: int, subscribers: int, *, now: datetime | None = None
) -> ViewWindows:
    current = now or datetime.now(UTC)
    run = await session.scalar(
        select(ViewCollectionRun)
        .where(
            ViewCollectionRun.channel_id == channel_id,
            ViewCollectionRun.complete.is_(True),
            ViewCollectionRun.error_code == "",
        )
        .order_by(ViewCollectionRun.observed_at.desc(), ViewCollectionRun.id.desc())
        .limit(1)
    )
    if (
        run is None
        or not run.complete
        or run.error_code
        or run.observed_at < current - timedelta(days=7)
        or run.coverage_start_at > current - timedelta(hours=72)
    ):
        return ViewWindows(None, None, None, None, run.observed_at if run else None)
    # A post's current lifetime total is not its 24-hour view count. Use the
    # first real observation after each age boundary, never extrapolate.
    samples = select(ViewPostObservation).where(
        ViewPostObservation.channel_id == channel_id,
        ViewPostObservation.observed_at >= current - timedelta(days=7),
        ViewPostObservation.observed_at <= current,
        or_(*(
            ViewPostObservation.observed_at.between(
                ViewPostObservation.published_at + timedelta(hours=hours),
                ViewPostObservation.published_at + timedelta(hours=hours) + SAMPLE_GRACE,
            )
            for hours in WINDOW_HOURS
        )),
    )
    rows = (await session.scalars(samples)).all()
    windows: dict[int, tuple[int | None, int]] = {}
    for hours in WINDOW_HOURS:
        boundary = timedelta(hours=hours)
        first_per_post: dict[int, ViewPostObservation] = {}
        for row in rows:
            age = row.observed_at - row.published_at
            if not boundary <= age <= boundary + SAMPLE_GRACE:
                continue
            previous = first_per_post.get(row.post_id)
            if previous is None or row.observed_at < previous.observed_at:
                first_per_post[row.post_id] = row
        count = len(first_per_post)
        windows[hours] = (
            round(sum(row.views for row in first_per_post.values()) / count) if count else None,
            count,
        )
    views24, posts24 = windows[24]
    er24 = round(100 * views24 / subscribers, 2) if subscribers > 0 and views24 is not None else None
    return ViewWindows(
        views24, windows[48][0], windows[72][0], er24, run.observed_at,
        posts24, windows[48][1], windows[72][1],
    )
