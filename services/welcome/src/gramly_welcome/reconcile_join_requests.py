from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from .db import engine, session_factory
from .join_request_policy import OPEN_JOIN_REQUEST_SQL_PREDICATE
from .models import InboxEvent, InboxSource, JoinRequest, ManagedBot, QueueStatus

LEGACY_INBOX_ERROR = "ProgrammingError"
LEGACY_APPROVAL_ERROR = "TelegramBadRequest"


@dataclass
class ReconciliationReport:
    inbox_events: int = 0
    approval_requests: int = 0
    superseded_requests: int = 0
    mode: str = "dry-run"


def _inbox_criteria() -> tuple[Any, ...]:
    return (
        InboxEvent.status.in_((QueueStatus.DEAD.value, QueueStatus.RETRY.value)),
        InboxEvent.last_error == LEGACY_INBOX_ERROR,
        InboxEvent.payload.op("?")("chat_join_request"),
    )


def _approval_criteria() -> tuple[Any, ...]:
    return (
        JoinRequest.status == "failed",
        JoinRequest.error == LEGACY_APPROVAL_ERROR,
        ManagedBot.auto_approve.is_(True),
        ManagedBot.is_active.is_(True),
    )


async def _recompute_sources(session: AsyncSession, source_keys: set[str]) -> None:
    for source_key in source_keys:
        count, next_available = (
            await session.execute(
                select(func.count(InboxEvent.id), func.min(InboxEvent.available_at)).where(
                    InboxEvent.source_key == source_key,
                    InboxEvent.status.in_(
                        (QueueStatus.PENDING.value, QueueStatus.RETRY.value)
                    ),
                )
            )
        ).one()
        source = await session.get(InboxSource, source_key)
        if source is not None:
            source.pending_count = int(count)
            source.next_available_at = next_available


async def reconcile(
    session: AsyncSession, *, apply: bool, batch_size: int = 500
) -> ReconciliationReport:
    report = ReconciliationReport(mode="apply" if apply else "dry-run")
    now = datetime.now(UTC)

    if not apply:
        report.inbox_events = int(
            await session.scalar(select(func.count(InboxEvent.id)).where(*_inbox_criteria()))
            or 0
        )
        report.approval_requests = int(
            await session.scalar(
                select(func.count(JoinRequest.id))
                .join(ManagedBot, ManagedBot.id == JoinRequest.bot_id)
                .where(*_approval_criteria())
            )
            or 0
        )
        return report

    inbox_candidates = list(
        (
            await session.scalars(
                select(InboxEvent)
                .where(
                    *_inbox_criteria(),
                )
                .order_by(InboxEvent.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    affected_events = inbox_candidates
    report.inbox_events = len(affected_events)

    approval_requests = list(
        (
            await session.scalars(
                select(JoinRequest)
                .join(ManagedBot, ManagedBot.id == JoinRequest.bot_id)
                .where(
                    *_approval_criteria(),
                )
                # When legacy failures contain more than one application for
                # the same contact, only the newest Telegram request can still
                # be meaningful. Older rows are closed below.
                .order_by(
                    JoinRequest.channel_id,
                    JoinRequest.contact_id,
                    JoinRequest.created_at.desc(),
                    JoinRequest.id.desc(),
                )
                .limit(batch_size)
                .with_for_update(of=JoinRequest, skip_locked=True)
            )
        ).all()
    )
    report.approval_requests = len(approval_requests)

    candidate_pairs = {
        (request.channel_id, request.contact_id) for request in approval_requests
    }
    occupied_pairs: set[tuple[int, int]] = set()
    if candidate_pairs:
        occupied_pairs = {
            (int(channel_id), int(contact_id))
            for channel_id, contact_id in (
                await session.execute(
                    select(JoinRequest.channel_id, JoinRequest.contact_id)
                    .where(
                        tuple_(JoinRequest.channel_id, JoinRequest.contact_id).in_(
                            candidate_pairs
                        ),
                        text(OPEN_JOIN_REQUEST_SQL_PREDICATE),
                    )
                    .with_for_update()
                )
            ).all()
        }

    source_keys: set[str] = set()
    for event in affected_events:
        source_keys.add(event.source_key)
        event.status = QueueStatus.RETRY.value
        event.attempts = 0
        event.available_at = now
        event.lease_owner = None
        event.lease_expires_at = None
        event.processed_at = None
        event.last_error = "reconciled_partial_index_conflict"

    for request in approval_requests:
        pair = (request.channel_id, request.contact_id)
        if pair in occupied_pairs:
            request.due_at = None
            request.lease_owner = None
            request.lease_expires_at = None
            request.processed_at = now
            request.error = "superseded_by_open_join_request"
            report.superseded_requests += 1
            continue
        occupied_pairs.add(pair)
        request.status = "scheduled"
        request.attempts = 0
        request.due_at = now
        request.lease_owner = None
        request.lease_expires_at = None
        request.processed_at = None
        request.error = "reconciled_legacy_bad_request"

    await session.flush()
    await _recompute_sources(session, source_keys)
    await session.commit()
    return report


async def _run(*, apply: bool, batch_size: int) -> ReconciliationReport:
    total = ReconciliationReport(mode="apply" if apply else "dry-run")
    async with session_factory() as session:
        while True:
            report = await reconcile(session, apply=apply, batch_size=batch_size)
            total.inbox_events += report.inbox_events
            total.approval_requests += report.approval_requests
            total.superseded_requests += report.superseded_requests
            if not apply or (
                report.inbox_events < batch_size
                and report.approval_requests < batch_size
            ):
                break
    await engine.dispose()
    return total


def run() -> None:
    parser = argparse.ArgumentParser(
        description="Recover join requests affected by the legacy partial-index conflict."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 5000:
        parser.error("--batch-size must be between 1 and 5000")
    report = asyncio.run(_run(apply=args.apply, batch_size=args.batch_size))
    print(json.dumps(asdict(report), sort_keys=True))


if __name__ == "__main__":
    run()
