"""Recent-post cohorts and separate measured post-age windows."""

from __future__ import annotations

from collections.abc import Sequence
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
    scanned_posts_count: int = 0
    exact_views_24h: int | None = None
    exact_views_48h: int | None = None
    exact_views_72h: int | None = None
    exact_posts_24h: int = 0
    exact_posts_48h: int = 0
    exact_posts_72h: int = 0
    last_error_code: str = ""


WINDOW_HOURS = (24, 48, 72)
SAMPLE_GRACE = timedelta(hours=2)


async def measured_windows(
    session: AsyncSession, channel_id: int, subscribers: int, *, now: datetime | None = None,
    include_exact: bool = True,
) -> ViewWindows:
    current = now or datetime.now(UTC)
    latest_attempt = await session.scalar(
        select(ViewCollectionRun)
        .where(ViewCollectionRun.channel_id == channel_id)
        .order_by(ViewCollectionRun.observed_at.desc(), ViewCollectionRun.id.desc())
        .limit(1)
    )
    last_error = latest_attempt.error_code if latest_attempt and latest_attempt.error_code else ""
    run = await session.scalar(
        select(ViewCollectionRun)
        .where(
            ViewCollectionRun.channel_id == channel_id,
            ViewCollectionRun.error_code == "",
        )
        .order_by(ViewCollectionRun.observed_at.desc(), ViewCollectionRun.id.desc())
        .limit(1)
    )
    if (
        run is None
        or run.error_code
        or run.observed_at < current - timedelta(hours=2)
    ):
        return ViewWindows(
            None, None, None, None, run.observed_at if run else None,
            scanned_posts_count=run.post_count if run else 0,
            last_error_code=last_error,
        )
    # The primary metric is current views of posts published in each trailing
    # period, taken from one complete collection. It is not views at post age.
    latest = select(ViewPostObservation).where(
        ViewPostObservation.channel_id == channel_id,
        ViewPostObservation.observed_at == run.observed_at,
        ViewPostObservation.published_at >= run.observed_at - timedelta(hours=72),
        ViewPostObservation.published_at <= run.observed_at,
    )
    latest_rows = (await session.scalars(latest)).all()
    latest_per_post = {
        row.post_id: row for row in latest_rows
        if row.observed_at == run.observed_at
        and run.observed_at - timedelta(hours=72) <= row.published_at <= run.observed_at
    }
    cohorts: dict[int, tuple[int | None, int]] = {}
    for hours in WINDOW_HOURS:
        cutoff = run.observed_at - timedelta(hours=hours)
        covered = run.complete or run.coverage_start_at <= cutoff
        values = [row.views for row in latest_per_post.values() if row.published_at >= cutoff] if covered else []
        cohorts[hours] = (round(sum(values) / len(values)) if values else None, len(values))

    # Preserve the exact-age measurements as secondary data. A post's current
    # lifetime total cannot be substituted for its views at age 24/48/72h.
    rows: Sequence[ViewPostObservation] = []
    if include_exact:
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
    views24, posts24 = cohorts[24]
    er24 = round(100 * views24 / subscribers, 2) if subscribers > 0 and views24 is not None else None
    return ViewWindows(
        views24, cohorts[48][0], cohorts[72][0], er24, run.observed_at,
        posts24, cohorts[48][1], cohorts[72][1], run.post_count,
        windows[24][0], windows[48][0], windows[72][0],
        windows[24][1], windows[48][1], windows[72][1],
        last_error,
    )
