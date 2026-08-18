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
from ..processor import process_event
from ..repository import claim_inbox_batch, finish_inbox_event, queue_snapshot, retry_inbox_event

logger = logging.getLogger(__name__)


async def process_claimed_event(event: InboxEvent, worker_id: str, max_attempts: int) -> None:
    try:
        async with session_factory() as session:
            async with session.begin():
                await process_event(session, event)
        async with session_factory() as session:
            await finish_inbox_event(session, event.id, worker_id)
        WORKER_EVENTS.labels("completed").inc()
    except Exception as exc:
        logger.exception(
            "event processing failed id=%s attempt=%s", event.id, event.attempts
        )
        async with session_factory() as session:
            await retry_inbox_event(
                session, event, worker_id, type(exc).__name__, max_attempts
            )
        WORKER_EVENTS.labels("retry").inc()


async def process_claimed_batch(
    events: list[InboxEvent], worker_id: str, max_attempts: int, concurrency: int
) -> None:
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(event: InboxEvent) -> None:
        async with semaphore:
            await process_claimed_event(event, worker_id, max_attempts)

    await asyncio.gather(*(guarded(event) for event in events))


async def serve() -> None:
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event_signal, stopping.set)
    WORKER_ACTIVE.labels("events").inc()
    next_metrics_refresh = 0.0
    try:
        while not stopping.is_set():
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
                    limit=settings.worker_batch_size,
                    lease_seconds=settings.lease_seconds,
                )
            if not events:
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
                continue
            await process_claimed_batch(
                list(events),
                worker_id,
                settings.max_attempts,
                settings.worker_concurrency,
            )
    finally:
        WORKER_ACTIVE.labels("events").dec()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    start_http_server(9090)
    asyncio.run(serve())


if __name__ == "__main__":
    run()
