"""Opt-in MTProto sampler for public channels already requested in GramlyView.

The user session is a secret supplied out of band. This job never logs it,
joins channels, stores message bodies, or increases Telegram view counters.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import delete, func, select
from telethon import TelegramClient, functions, utils

from .db import session_factory
from .models import ViewChannelSnapshot, ViewCollectionRun, ViewPostObservation, ViewPrivateChannel
from .repository import queue_snapshot
from .view_mentions import active_monitors, dispatch_pending, mentioned_usernames, record_mentions
from .view_pool import analytics_available, lease_account

logger = logging.getLogger(__name__)
MAX_CHANNELS_PER_RUN = 20
MAX_POSTS_PER_CHANNEL = 500


async def collect_one(client: TelegramClient, username: object, expected_id: int) -> bool:
    observed = datetime.now(UTC)
    # Keep the whole two-hour sampling window after a post reaches age 72h.
    cutoff = observed - timedelta(hours=75)
    posts: list[ViewPostObservation] = []
    mention_posts: list[tuple[int, datetime, str, list[str]]] = []
    complete = False
    seen_count = 0
    eligible_count = 0
    oldest_seen_at: datetime | None = None
    channel_id = expected_id
    try:
        entity = await client.get_entity(username)
        channel_id = utils.get_peer_id(entity)
        if channel_id != expected_id:
            # Usernames can move. Never attach the new channel's data to the
            # old Bot API channel identity.
            raise ValueError("channel_identity_changed")
        source_username = str(getattr(entity, "username", None) or "").lower()
        async for message in client.iter_messages(entity, limit=MAX_POSTS_PER_CHANNEL):
            seen_count += 1
            if message.date is None:
                continue
            oldest_seen_at = min(oldest_seen_at, message.date) if oldest_seen_at else message.date
            if message.date < cutoff:
                complete = True
                break
            eligible_count += 1
            if source_username:
                mention_posts.append((
                    message.id, message.date, str(getattr(message, "message", None) or ""),
                    [str(entity.url) for entity in (getattr(message, "entities", None) or ())
                     if getattr(entity, "url", None)],
                ))
            if message.views is None:
                continue
            posts.append(
                ViewPostObservation(
                    channel_id=channel_id,
                    post_id=message.id,
                    published_at=message.date,
                    observed_at=observed,
                    views=message.views,
                    forwards=message.forwards,
                    replies_count=getattr(getattr(message, "replies", None), "replies", None),
                    reactions_count=(
                        sum(int(result.count) for result in (message.reactions.results or ()))
                        if getattr(message, "reactions", None) is not None else None
                    ),
                )
            )
        else:
            # Reaching the limit cannot prove full 72-hour coverage. An
            # exhausted short channel can; the conservative choice is to
            # require an older boundary post when the limit is reached.
            complete = seen_count < MAX_POSTS_PER_CHANNEL
    except Exception as exc:
        logger.warning("View collection failed channel_id=%s class=%s", expected_id, type(exc).__name__)
        async with session_factory() as session, session.begin():
            session.add(
                ViewCollectionRun(
                    channel_id=expected_id,
                    observed_at=observed,
                    coverage_start_at=cutoff,
                    complete=False,
                    post_count=0,
                    error_code=type(exc).__name__[:48],
                )
            )
        return False
    async with session_factory() as session, session.begin():
        session.add_all(posts)
        if mention_posts:
            names = {
                name for _post_id, _published_at, text, links in mention_posts
                for name in mentioned_usernames(text, links)
            }
            monitors = await active_monitors(session, names)
            await record_mentions(
                session, monitors, source_channel_id=channel_id,
                source_username=source_username, posts=mention_posts,
            )
        session.add(
            ViewCollectionRun(
                channel_id=channel_id,
                observed_at=observed,
                coverage_start_at=cutoff if complete else (oldest_seen_at or observed),
                complete=complete,
                post_count=eligible_count,
                error_code="",
            )
        )
    return complete


async def collect() -> None:
    if not await analytics_available():
        logger.warning("GramlyView collection paused: fewer than three healthy accounts")
        return
    async with session_factory() as session:
        last_run = (
            select(ViewCollectionRun.channel_id, func.max(ViewCollectionRun.observed_at).label("last_at"))
            .group_by(ViewCollectionRun.channel_id)
            .subquery()
        )
        public = (
            select(
                ViewChannelSnapshot.channel_id,
                func.max(ViewChannelSnapshot.username).label("username"),
            )
            .where(
                ViewChannelSnapshot.username != "",
                ViewChannelSnapshot.observed_at >= datetime.now(UTC) - timedelta(days=7),
            )
            .group_by(ViewChannelSnapshot.channel_id)
            .subquery()
        )
        rows = (
            await session.execute(
                select(public.c.channel_id, public.c.username, last_run.c.last_at)
                .outerjoin(last_run, last_run.c.channel_id == public.c.channel_id)
                .order_by(last_run.c.last_at.asc().nullsfirst(), public.c.channel_id)
                .limit(MAX_CHANNELS_PER_RUN)
            )
        ).all()
        private_candidates = (
            select(
                ViewPrivateChannel.channel_id,
                func.min(ViewPrivateChannel.account_id).label("account_id"),
            )
            .where(ViewPrivateChannel.status == "active")
            .group_by(ViewPrivateChannel.channel_id)
            .subquery()
        )
        private = (
            await session.execute(
                select(private_candidates.c.channel_id, private_candidates.c.account_id, last_run.c.last_at)
                .outerjoin(last_run, last_run.c.channel_id == private_candidates.c.channel_id)
                .order_by(last_run.c.last_at.asc().nullsfirst(), private_candidates.c.channel_id)
                .limit(MAX_CHANNELS_PER_RUN)
            )
        ).all()
    scheduled = sorted(
        [("public", channel_id, username, last_at) for channel_id, username, last_at in rows]
        + [("private", channel_id, account_id, last_at) for channel_id, account_id, last_at in private],
        key=lambda item: item[3] or datetime.min.replace(tzinfo=UTC),
    )[:MAX_CHANNELS_PER_RUN]
    # Cron has an 840-second deadline; leave room for dispatch and cleanup.
    stop_at = time.monotonic() + 720
    for kind, channel_id, target, _last_at in scheduled:
        if time.monotonic() + 35 >= stop_at:
            break
        try:
            async with session_factory() as session:
                hello_queues = await queue_snapshot(session)
            if any(hello_queues[name][2] > 5 for name in (
                "deliveries", "approvals", "join_activations", "content_operations"
            )):
                logger.warning("View collection deferred: Hello delivery is behind schedule")
                break
        except Exception as exc:
            logger.warning("View collection deferred: Hello health unavailable class=%s", type(exc).__name__)
            break
        try:
            async with asyncio.timeout(30):
                async with lease_account(cast(int, target) if kind == "private" else None) as (_id, client):
                    entity = await client.get_entity(channel_id if kind == "private" else target)
                    if utils.get_peer_id(entity) != channel_id or not getattr(entity, "broadcast", False):
                        raise ValueError("channel_identity_changed")
                    await collect_one(client, entity, channel_id)
                    full = await client(functions.channels.GetFullChannelRequest(channel=entity))
                    raw_count = getattr(full.full_chat, "participants_count", None)
                    if raw_count is not None:
                        async with session_factory() as session, session.begin():
                            session.add(ViewChannelSnapshot(
                                channel_id=channel_id,
                                username=str(getattr(entity, "username", None) or ""),
                                title=str(getattr(entity, "title", "Канал"))[:255],
                                subscriber_count=int(raw_count),
                                observed_at=datetime.now(UTC),
                            ))
        except Exception as exc:
            logger.warning("View collection unavailable channel_id=%s kind=%s class=%s", channel_id, kind, type(exc).__name__)
            async with session_factory() as session, session.begin():
                session.add(ViewCollectionRun(
                    channel_id=channel_id,
                    observed_at=datetime.now(UTC),
                    coverage_start_at=datetime.now(UTC) - timedelta(hours=75),
                    complete=False,
                    post_count=0,
                    error_code=type(exc).__name__[:48],
                ))
        # Flush alerts after each source: a slow later scan must not delay
        # earlier findings until the end of this collector run.
        try:
            await dispatch_pending(limit=5, time_budget_seconds=10)
        except Exception as exc:
            logger.warning("View mention dispatch deferred class=%s", type(exc).__name__)
    # Bound storage without a long delete transaction. The 7-day samples
    # support short trends; older numeric observations are not needed for the
    # 72-hour cards. Repeated runs gradually drain any accumulated backlog.
    cutoff = datetime.now(UTC) - timedelta(days=7)
    for _ in range(5):
        async with session_factory() as session, session.begin():
            ids = (
                select(ViewPostObservation.id)
                .where(ViewPostObservation.observed_at < cutoff)
                .order_by(ViewPostObservation.id)
                .limit(10_000)
            )
            result = await session.execute(delete(ViewPostObservation).where(ViewPostObservation.id.in_(ids)))
            if not getattr(result, "rowcount", 0):
                break
    try:
        await dispatch_pending(time_budget_seconds=60)
    except Exception as exc:
        logger.warning("View mention dispatch deferred class=%s", type(exc).__name__)


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(collect())
