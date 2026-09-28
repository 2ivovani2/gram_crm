"""Opt-in mention alerts from the already indexed public-channel corpus.

Only IDs and links are persisted. Post bodies are inspected in memory and
never logged or retained. A private source channel is never indexed here.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db import session_factory
from .models import Owner, ViewMentionEvent, ViewMentionMonitor

logger = logging.getLogger(__name__)
USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
MENTION = re.compile(
    r"(?<![\w/])@([A-Za-z][A-Za-z0-9_]{4,31})\b|"
    r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{4,31})\b",
    re.I,
)
MAX_MONITORS_PER_OWNER = 20


def normalized_username(username: str) -> str:
    value = username.strip().lstrip("@").lower()
    if not USERNAME.fullmatch(value):
        raise ValueError("Для мониторинга нужен публичный @username канала")
    return value


def mentioned_usernames(text: str, entity_urls: Iterable[str] = ()) -> set[str]:
    """Match visible and hidden Telegram links, without retaining their text."""
    return {
        (match.group(1) or match.group(2)).lower()
        for item in (text, *entity_urls)
        for match in MENTION.finditer(item)
    }


async def subscribe_mentions(owner_id: int, channel_id: int, username: str) -> None:
    value = normalized_username(username)
    async with session_factory() as session, session.begin():
        owner = await session.get(Owner, owner_id, with_for_update=True)
        if owner is None:
            raise ValueError("Пользователь не найден")
        existing = await session.scalar(select(ViewMentionMonitor).where(
            ViewMentionMonitor.owner_id == owner_id,
            ViewMentionMonitor.channel_id == channel_id,
        ))
        if existing is None or not existing.enabled:
            count = int(await session.scalar(select(func.count()).select_from(ViewMentionMonitor).where(
                ViewMentionMonitor.owner_id == owner_id,
                ViewMentionMonitor.enabled.is_(True),
            )) or 0)
            if count >= MAX_MONITORS_PER_OWNER:
                raise ValueError(f"Можно отслеживать не больше {MAX_MONITORS_PER_OWNER} каналов")
        statement = insert(ViewMentionMonitor).values(
            owner_id=owner_id, channel_id=channel_id, username=value, enabled=True
        )
        await session.execute(statement.on_conflict_do_update(
            constraint="uq_view_mention_monitor_owner_channel",
            set_={
                "username": value,
                "enabled": True,
                "created_at": case(
                    (ViewMentionMonitor.enabled.is_(False), func.now()),
                    (ViewMentionMonitor.username != value, func.now()),
                    else_=ViewMentionMonitor.created_at,
                ),
            },
        ))


async def unsubscribe_mentions(owner_id: int, channel_id: int) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(update(ViewMentionMonitor).where(
            ViewMentionMonitor.owner_id == owner_id,
            ViewMentionMonitor.channel_id == channel_id,
        ).values(enabled=False))


async def list_mention_monitors(owner_id: int) -> list[ViewMentionMonitor]:
    async with session_factory() as session:
        return list((await session.scalars(select(ViewMentionMonitor).where(
            ViewMentionMonitor.owner_id == owner_id,
            ViewMentionMonitor.enabled.is_(True),
        ).order_by(ViewMentionMonitor.created_at.desc()).limit(MAX_MONITORS_PER_OWNER))).all())


async def mention_count_7d(owner_id: int, channel_id: int) -> int | None:
    """Return None without opt-in; zero only means none in the indexed corpus."""
    async with session_factory() as session:
        monitor = await session.scalar(select(ViewMentionMonitor).where(
            ViewMentionMonitor.owner_id == owner_id,
            ViewMentionMonitor.channel_id == channel_id,
            ViewMentionMonitor.enabled.is_(True),
        ))
        if monitor is None:
            return None
        distinct_posts = select(
            ViewMentionEvent.source_channel_id, ViewMentionEvent.post_id,
        ).where(
            ViewMentionEvent.monitor_id == monitor.id,
            ViewMentionEvent.published_at >= datetime.now(UTC) - timedelta(days=7),
        ).distinct().subquery()
        return int(await session.scalar(select(func.count()).select_from(distinct_posts)) or 0)


async def active_monitors(session: AsyncSession, usernames: set[str]) -> list[ViewMentionMonitor]:
    if not usernames:
        return []
    return list((await session.scalars(select(ViewMentionMonitor).where(
        ViewMentionMonitor.enabled.is_(True),
        func.lower(ViewMentionMonitor.username).in_(usernames),
    ))).all())


async def record_mentions(
    session: AsyncSession,
    monitors: list[ViewMentionMonitor],
    *,
    source_channel_id: int,
    source_username: str,
    posts: Iterable[tuple[int, datetime, str, Iterable[str]]],
) -> int:
    """Persist new post references only; caller owns the transaction."""
    if not source_username or not monitors:
        return 0
    by_username: dict[str, list[ViewMentionMonitor]] = {}
    for monitor in monitors:
        if monitor.channel_id != source_channel_id:
            by_username.setdefault(monitor.username.lower(), []).append(monitor)
    values = []
    for post_id, published_at, text, entity_urls in posts:
        for username in mentioned_usernames(text, entity_urls):
            for monitor in by_username.get(username, ()):
                if published_at >= monitor.created_at:
                    values.append({
                        "monitor_id": monitor.id,
                        "source_channel_id": source_channel_id,
                        "source_username": source_username,
                        "post_id": post_id,
                        "published_at": published_at,
                    })
    if values:
        await session.execute(insert(ViewMentionEvent).values(values).on_conflict_do_nothing(
            constraint="uq_view_mention_event_post"
        ))
    return len(values)


async def _dispatch_batch(limit: int, gate_cache: dict[tuple[int, int], tuple[bool, bool]]) -> tuple[int, int]:
    """Claim one batch, then release DB connections before Telegram calls."""
    from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

    from .required_channel import ALLOWED_STATUSES, _member_allowed, _status_value, required_channel_config
    from .view_bot import _send, view_bot

    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        events = list((await session.scalars(select(ViewMentionEvent).where(
            ViewMentionEvent.notified_at.is_(None),
            ViewMentionEvent.attempts < 5,
            (ViewMentionEvent.claim_until.is_(None) | (ViewMentionEvent.claim_until <= now)),
        ).order_by(ViewMentionEvent.id).with_for_update(skip_locked=True).limit(limit))).all())
        claims = [(event.id, now + timedelta(minutes=10)) for event in events]
        for event, (_, until) in zip(events, claims, strict=True):
            event.claim_until = until
    delivered = 0
    for event_id, claim_until in claims:
        transient_gate = False
        async with session_factory() as session:
            row = await session.get(ViewMentionEvent, event_id)
            monitor = await session.get(ViewMentionMonitor, row.monitor_id) if row else None
            owner = await session.get(Owner, monitor.owner_id) if monitor else None
            if row is None or monitor is None or owner is None or not monitor.enabled:
                should_send = False
                terminal = True
            else:
                config = await required_channel_config(session)
                gate_enabled = config.enabled
                gate_channel_id = config.channel_id
                source = row.source_username
                target = monitor.username
                post_id = row.post_id
                chat_id = owner.telegram_id
                terminal = False
        if not terminal:
            if not gate_enabled:
                should_send = True
            elif not gate_channel_id:
                should_send, transient_gate = False, True
            elif (gate_channel_id, chat_id) in gate_cache:
                should_send, transient_gate = gate_cache[(gate_channel_id, chat_id)]
            else:
                try:
                    member = await asyncio.wait_for(
                        view_bot().get_chat_member(gate_channel_id, chat_id), timeout=4,
                    )
                    status = _status_value(member)
                    should_send = _member_allowed(
                        status, bool(getattr(member, "is_member", status in ALLOWED_STATUSES))
                    )
                except (TelegramAPIError, TimeoutError) as exc:
                    logger.warning("GramlyView mention gate failed event_id=%s class=%s", event_id, type(exc).__name__)
                    should_send, transient_gate = False, True
                gate_cache[(gate_channel_id, chat_id)] = (should_send, transient_gate)
        sent = False
        if not should_send:
            terminal = terminal or not transient_gate
        else:
            try:
                await asyncio.wait_for(_send(
                    chat_id,
                    f"<b>Новое упоминание @{html.escape(target)}</b>\n"
                    f'Канал: <a href="https://t.me/{html.escape(source)}">@{html.escape(source)}</a>\n'
                    f'<a href="https://t.me/{html.escape(source)}/{post_id}">Открыть публикацию</a>\n'
                    "<blockquote>Найдено среди отслеживаемых публичных каналов GramlyView. "
                    "Это не полный поиск по Telegram.</blockquote>",
                ), timeout=4)
                sent = True
            except TelegramForbiddenError:
                terminal = True
            except (TelegramAPIError, TimeoutError) as exc:
                logger.warning("GramlyView mention delivery failed event_id=%s class=%s", event_id, type(exc).__name__)
        async with session_factory() as session, session.begin():
            current = await session.get(ViewMentionEvent, event_id, with_for_update=True)
            if current is not None and current.claim_until == claim_until:
                if sent or terminal:
                    current.notified_at = datetime.now(UTC)
                    delivered += int(sent)
                elif should_send:
                    current.attempts += 1
                current.claim_until = None if sent or terminal else datetime.now(UTC) + timedelta(minutes=5)
        await asyncio.sleep(0.05)
    return delivered, len(claims)


async def dispatch_pending(limit: int = 20, time_budget_seconds: int = 180) -> int:
    """Drain in bounded batches within a collector cycle; retry failures later."""
    deadline = time.monotonic() + time_budget_seconds
    gate_cache: dict[tuple[int, int], tuple[bool, bool]] = {}
    delivered = 0
    while time.monotonic() < deadline:
        sent, claimed = await _dispatch_batch(limit, gate_cache)
        delivered += sent
        if claimed < limit:
            break
    return delivered
