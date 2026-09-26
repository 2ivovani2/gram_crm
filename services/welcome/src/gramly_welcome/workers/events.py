from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid
from time import monotonic

from prometheus_client import start_http_server

from ..config import get_settings
from ..db import session_factory
from ..metrics import OLDEST_PENDING_AGE, QUEUE_DEPTH, WORKER_ACTIVE, WORKER_EVENTS
from ..models import InboxEvent
from ..owner_bot import process_interface_update
from ..processor import is_actionable_payload, process_event
from ..repository import (
    claim_inbox_batch,
    finish_inbox_event,
    finish_inbox_events,
    queue_snapshot,
    retry_inbox_event,
)
from ..view_bot import process_view_update
from .health import WorkerHealth
from .leased import run_leased

logger = logging.getLogger(__name__)


async def process_claimed_event(event: InboxEvent, worker_id: str, max_attempts: int) -> None:
    try:
        if getattr(event, "source_key", "interface") == "view":
            await process_view_update(event.payload)
            async with session_factory() as session:
                async with session.begin():
                    await finish_inbox_event(session, event.id, worker_id)
        elif event.bot_id is None:
            # Owner-facing bot is part of the standalone Welcome product too.
            # Telegram I/O happens outside a database transaction; the durable
            # inbox lease remains the retry boundary.
            await process_interface_update(event.payload)
            async with session_factory() as session:
                async with session.begin():
                    await finish_inbox_event(session, event.id, worker_id)
        else:
            async with session_factory() as session:
                async with session.begin():
                    await process_event(session, event)
                    await finish_inbox_event(session, event.id, worker_id)
        WORKER_EVENTS.labels("completed").inc()
    except Exception as exc:
        logger.warning("event processing failed id=%s attempt=%s class=%s", event.id, event.attempts, type(exc).__name__)
        async with session_factory() as session:
            await retry_inbox_event(session, event, worker_id, type(exc).__name__, max_attempts)
        WORKER_EVENTS.labels("retry").inc()


async def process_claimed_batch(
    events: list[InboxEvent], worker_id: str, max_attempts: int, concurrency: int
) -> None:
    # Unsupported client updates have no business side effects. A single
    # guarded UPDATE acknowledges the whole slice instead of opening one
    # transaction per event. Interface events are deliberately excluded until
    # their control-plane consumer is enabled during cutover.
    discardable = [
        event for event in events if event.bot_id is not None and not is_actionable_payload(event.payload)
    ]
    discardable_ids = {event.id for event in discardable}
    actionable = [event for event in events if event.id not in discardable_ids]
    if discardable:
        async with session_factory() as session:
            async with session.begin():
                completed = await finish_inbox_events(session, [event.id for event in discardable], worker_id)
        WORKER_EVENTS.labels("completed").inc(completed)

    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(event: InboxEvent) -> None:
        async with semaphore:
            await run_leased(process_claimed_event(event, worker_id, max_attempts), InboxEvent, event.id, worker_id)

    interface_events = sorted(
        (event for event in actionable if event.bot_id is None), key=lambda event: event.update_id
    )
    client_events = [event for event in actionable if event.bot_id is not None]
    # Preserve the conversational order of the owner bot inside each claimed
    # batch. High-volume customer bot events remain concurrent.
    for event in interface_events:
        await process_claimed_event(event, worker_id, max_attempts)
    await asyncio.gather(*(guarded(event) for event in client_events))


async def serve() -> None:
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event_signal, stopping.set)
    WORKER_ACTIVE.labels("events").inc()
    health = WorkerHealth({"events": 150})
    server = health.start()
    next_metrics_refresh = 0.0
    try:
        while not stopping.is_set():
            health.mark("events")
            try:
                if monotonic() >= next_metrics_refresh:
                    async with session_factory() as session:
                        snapshot = await queue_snapshot(session)
                    for queue, (depth, dead, age) in snapshot.items():
                        QUEUE_DEPTH.labels(queue, "actionable").set(depth)
                        QUEUE_DEPTH.labels(queue, "dead").set(dead)
                        OLDEST_PENDING_AGE.labels(queue).set(age)
                    next_metrics_refresh = monotonic() + 10
                async with session_factory() as session:
                    events = await claim_inbox_batch(
                        session,
                        worker_id=worker_id,
                        limit=min(settings.worker_batch_size, settings.worker_concurrency),
                        lease_seconds=180,
                        include_owner=not settings.separate_owner_worker,
                        # Long View scans have a dedicated consumer. Never let
                        # them occupy the Welcome event worker's capacity.
                        include_view=False,
                    )
                await process_claimed_batch(
                    list(events), worker_id, settings.max_attempts, settings.worker_concurrency,
                )
                health.mark("events", healthy=True)
            except Exception as exc:
                health.mark("events", healthy=False)
                logger.warning("Event queue dependency/retry class=%s", type(exc).__name__)
                await asyncio.sleep(2)
                continue
            if not events:
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
                continue
    finally:
        server.shutdown()
        WORKER_ACTIVE.labels("events").dec()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    start_http_server(9090)
    asyncio.run(serve())


if __name__ == "__main__":
    run()
