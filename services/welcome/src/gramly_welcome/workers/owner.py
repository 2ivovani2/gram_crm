"""Independent, per-actor ordered owner inbox and durable UI response workers."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from datetime import UTC, datetime, timedelta

from prometheus_client import start_http_server
from sqlalchemy import BigInteger, and_, cast, exists, func, or_, select, update
from sqlalchemy.orm import aliased

from ..config import get_settings
from ..db import independent_session
from ..metrics import OLDEST_PENDING_AGE, OWNER_ACTIONS, OWNER_HEARTBEAT, QUEUE_DEPTH
from ..models import InboxEvent, InboxSource, OwnerReply
from ..owner_actions import run_owner_action
from ..owner_bot import interface_bot
from ..owner_reply import deliver_next_reply
from ..repository import finish_inbox_event, retry_inbox_event
from .health import WorkerHealth

logger = logging.getLogger(__name__)


async def refresh_metrics() -> None:
    now = datetime.now(UTC)
    async with independent_session() as session:
        for name, model, date, conditions in (
            (
                "owner",
                InboxEvent,
                InboxEvent.received_at,
                [InboxEvent.source_key == "interface", InboxEvent.actor_id.is_not(None)],
            ),
            ("owner_reply", OwnerReply, OwnerReply.created_at, []),
        ):
            pending = ["pending", "retry", "processing", "sending"]
            rows = (
                await session.execute(
                    select(model.status, func.count(), func.min(date))
                    .where(*conditions)
                    .group_by(model.status)
                )
            ).all()
            count, age = 0, 0.0
            for status, size, oldest in rows:
                QUEUE_DEPTH.labels(name, status).set(size)
                if status in pending:
                    count += size
                    age = max(age, (now - oldest).total_seconds())
            for status in pending:
                if status not in {row[0] for row in rows}:
                    QUEUE_DEPTH.labels(name, status).set(0)
            QUEUE_DEPTH.labels(name, "actionable").set(count)
            OLDEST_PENDING_AGE.labels(name).set(age)


async def claim_owner_event(worker_id: str) -> InboxEvent | None:
    settings = get_settings()
    now = datetime.now(UTC)
    earlier = aliased(InboxEvent)
    # An old API pod may insert one last update without actor_id during a
    # rolling upgrade. It must still participate in per-user ordering.
    actor = func.coalesce(
        InboxEvent.actor_id,
        *(
            cast(InboxEvent.payload[name]["from"]["id"].astext, BigInteger)
            for name in ("message", "callback_query", "pre_checkout_query", "chat_member", "my_chat_member")
        ),
        -InboxEvent.update_id,
    )
    earlier_actor = func.coalesce(
        earlier.actor_id,
        *(
            cast(earlier.payload[name]["from"]["id"].astext, BigInteger)
            for name in ("message", "callback_query", "pre_checkout_query", "chat_member", "my_chat_member")
        ),
        -earlier.update_id,
    )
    async with independent_session() as session, session.begin():
        event = await session.scalar(
            select(InboxEvent)
            .where(
                InboxEvent.source_key != "view",
                InboxEvent.bot_id.is_(None),
                or_(
                    and_(InboxEvent.status.in_(["pending", "retry"]), InboxEvent.available_at <= now),
                    and_(InboxEvent.status == "processing", InboxEvent.lease_expires_at < now),
                ),
                ~exists(
                    select(earlier.id).where(
                        earlier.source_key == InboxEvent.source_key,
                        earlier_actor == actor,
                        earlier.update_id < InboxEvent.update_id,
                        earlier.status.in_(["pending", "retry", "processing"]),
                    )
                ),
            )
            .order_by(InboxEvent.available_at, InboxEvent.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if event is None:
            return None
        if event.status != "processing":
            await session.execute(
                update(InboxSource)
                .where(InboxSource.source_key == event.source_key)
                .values(
                    pending_count=func.greatest(0, InboxSource.pending_count - 1),
                )
            )
        else:
            OWNER_ACTIONS.labels("lease_recovered").inc()
        event.status = "processing"
        event.lease_owner = worker_id
        event.lease_expires_at = now + timedelta(seconds=settings.owner_lease_seconds)
        event.attempts += 1
        return event


async def renew_lease(event_id: int, worker_id: str) -> None:
    while True:
        await asyncio.sleep(20)
        async with independent_session() as session, session.begin():
            renewed = await session.scalar(
                update(InboxEvent)
                .where(
                    InboxEvent.id == event_id,
                    InboxEvent.lease_owner == worker_id,
                    InboxEvent.status == "processing",
                )
                .values(
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=get_settings().owner_lease_seconds)
                )
                .returning(InboxEvent.id)
            )
            if renewed is None:
                raise RuntimeError("Owner event lease was lost")


async def process(event: InboxEvent, worker_id: str) -> None:
    renewal = asyncio.create_task(renew_lease(event.id, worker_id))
    action = asyncio.create_task(run_owner_action(event.payload))
    try:
        async with asyncio.timeout(get_settings().owner_action_timeout_seconds):
            done, _ = await asyncio.wait((renewal, action), return_when=asyncio.FIRST_COMPLETED)
            if renewal in done:
                await renewal
                raise RuntimeError("Owner lease renewal stopped")
            outcome = await action
        async with independent_session() as session, session.begin():
            await finish_inbox_event(session, event.id, worker_id)
        OWNER_ACTIONS.labels(outcome).inc()
    except Exception as exc:
        action.cancel()
        await asyncio.gather(action, return_exceptions=True)
        # Payload, Telegram usernames and provider exceptions are not logged.
        logger.warning("Owner action retry class=%s", type(exc).__name__)
        async with independent_session() as session:
            await retry_inbox_event(
                session, event, worker_id, type(exc).__name__, get_settings().max_attempts
            )
        OWNER_ACTIONS.labels("retry").inc()
    finally:
        action.cancel()
        renewal.cancel()
        await asyncio.gather(action, renewal, return_exceptions=True)


async def serve() -> None:
    if not get_settings().separate_owner_worker:
        raise RuntimeError("Owner workers require WELCOME_SEPARATE_OWNER_WORKER=true")
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)
    health = WorkerHealth(
        {
            **{
                f"action:{i}": get_settings().owner_action_timeout_seconds + 30
                for i in range(get_settings().owner_concurrency)
            },
            **{f"reply:{i}": 150 for i in range(2)},
        }
    )
    server = health.start()

    async def metrics_loop() -> None:
        while not stopping.is_set():
            try:
                await refresh_metrics()
            except Exception as exc:
                logger.warning("Owner queue metrics unavailable class=%s", type(exc).__name__)
            try:
                await asyncio.wait_for(stopping.wait(), timeout=10)
            except TimeoutError:
                pass

    async def consumer(index: int, *, replies: bool = False) -> None:
        worker_id = f"{socket.gethostname()}:{os.getpid()}:{index}"
        slot = f"{'reply' if replies else 'action'}:{index}"
        while not stopping.is_set():
            health.mark(slot)
            OWNER_HEARTBEAT.labels("reply" if replies else "action", str(index)).set_to_current_time()
            try:
                if replies:
                    worked = await deliver_next_reply(interface_bot())
                else:
                    event = await claim_owner_event(worker_id)
                    worked = event is not None
                    if event is not None:
                        await process(event, worker_id)
                health.mark(slot, healthy=True)
                if not worked:
                    await asyncio.sleep(0.25)
            except Exception as exc:
                health.mark(slot, healthy=False)
                logger.warning("Owner loop dependency unavailable class=%s", type(exc).__name__)
                OWNER_ACTIONS.labels("dependency_retry").inc()
                await asyncio.sleep(2)
        OWNER_HEARTBEAT.labels("reply" if replies else "action", str(index)).set(0)

    try:
        await asyncio.gather(
            *(consumer(i) for i in range(get_settings().owner_concurrency)),
            *(consumer(i, replies=True) for i in range(2)),
            metrics_loop(),
        )
    finally:
        server.shutdown()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    start_http_server(9090)
    asyncio.run(serve())


if __name__ == "__main__":
    run()
