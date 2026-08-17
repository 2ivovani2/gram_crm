from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid

from ..config import get_settings
from ..db import session_factory
from ..metrics import WORKER_ACTIVE, WORKER_EVENTS
from ..processor import process_event
from ..repository import claim_inbox_batch, finish_inbox_event, retry_inbox_event

logger = logging.getLogger(__name__)


async def serve() -> None:
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event_signal, stopping.set)
    WORKER_ACTIVE.labels("events").inc()
    try:
        while not stopping.is_set():
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
            for event in events:
                if stopping.is_set():
                    break
                try:
                    async with session_factory() as session:
                        async with session.begin():
                            await process_event(session, event)
                    async with session_factory() as session:
                        await finish_inbox_event(session, event.id, worker_id)
                    WORKER_EVENTS.labels("completed").inc()
                except Exception as exc:
                    logger.exception("event processing failed id=%s attempt=%s", event.id, event.attempts)
                    async with session_factory() as session:
                        await retry_inbox_event(
                            session, event, worker_id, type(exc).__name__, settings.max_attempts
                        )
                    WORKER_EVENTS.labels("retry").inc()
    finally:
        WORKER_ACTIVE.labels("events").dec()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())


if __name__ == "__main__":
    run()
