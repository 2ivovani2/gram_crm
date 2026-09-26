"""Opt-in MTProto sampler for public channels already requested in GramlyView.

The user session is a secret supplied out of band. This job never logs it,
joins channels, stores message bodies, or increases Telegram view counters.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import delete, func, select
from telethon import TelegramClient, functions, utils

from .db import session_factory
from .models import ViewChannelSnapshot, ViewCollectionRun, ViewPostObservation, ViewPrivateChannel
from .view_pool import analytics_available, lease_account

logger = logging.getLogger(__name__)
MAX_CHANNELS_PER_RUN = 20
MAX_POSTS_PER_CHANNEL = 500


async def collect_one(client: TelegramClient, username: object, expected_id: int) -> bool:
    observed = datetime.now(UTC)
    cutoff = observed - timedelta(hours=73)
    posts: list[ViewPostObservation] = []
    complete = False
    seen_count = 0
    channel_id = expected_id
    try:
        entity = await client.get_entity(username)
        channel_id = utils.get_peer_id(entity)
        if channel_id != expected_id:
            # Usernames can move. Never attach the new channel's data to the
            # old Bot API channel identity.
            raise ValueError("channel_identity_changed")
        async for message in client.iter_messages(entity, limit=MAX_POSTS_PER_CHANNEL):
            seen_count += 1
            if message.date is None:
                continue
            if message.date < cutoff:
                complete = True
                break
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
        session.add(
            ViewCollectionRun(
                channel_id=channel_id,
                observed_at=observed,
                coverage_start_at=cutoff,
                complete=complete,
                post_count=len(posts),
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
    for kind, channel_id, target, _last_at in scheduled:
        try:
            async with lease_account(cast(int, target) if kind == "private" else None) as (_id, client):
                entity = await client.get_entity(channel_id if kind == "private" else target)
                if utils.get_peer_id(entity) != channel_id or not getattr(entity, "broadcast", False):
                    raise ValueError("channel_identity_changed")
                await asyncio.wait_for(collect_one(client, entity, channel_id), timeout=180)
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
                    coverage_start_at=datetime.now(UTC) - timedelta(hours=73),
                    complete=False,
                    post_count=0,
                    error_code=type(exc).__name__[:48],
                ))
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


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(collect())
