"""Isolated consumer for GramlyView's potentially long MTProto scans.

One pod claims bounded batches. Updates from one person remain ordered, while
different people can be served concurrently without occupying Hello workers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid
from collections import defaultdict

from prometheus_client import start_http_server

from ..config import get_settings
from ..db import session_factory
from ..models import InboxEvent
from ..repository import claim_inbox_batch
from .events import process_claimed_event
from .health import WorkerHealth
from .leased import run_leased

logger = logging.getLogger(__name__)
VIEW_CONCURRENCY = 4
VIEW_TIMEOUT_SECONDS = 900


async def process_view_batch(events: list[InboxEvent], worker_id: str, max_attempts: int) -> None:
    by_actor: dict[int, list[InboxEvent]] = defaultdict(list)
    for event in events:
        by_actor[event.actor_id if event.actor_id is not None else -event.id].append(event)

    async def process_actor(actor_events: list[InboxEvent]) -> None:
        for event in sorted(actor_events, key=lambda item: item.update_id):
            await run_leased(
                process_claimed_event(event, worker_id, max_attempts),
                InboxEvent,
                event.id,
                worker_id,
                timeout_seconds=VIEW_TIMEOUT_SECONDS,
            )

    await asyncio.gather(*(process_actor(actor_events) for actor_events in by_actor.values()))


async def serve() -> None:
    settings = get_settings()
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)
    worker_id = f"view:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    health = WorkerHealth({"view": 90})
    server = health.start()

    async def heartbeat() -> None:
        while not stopping.is_set():
            health.mark("view")
            try:
                await asyncio.wait_for(stopping.wait(), timeout=15)
            except TimeoutError:
                pass

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        while not stopping.is_set():
            try:
                async with session_factory() as session:
                    events = await claim_inbox_batch(
                        session,
                        worker_id=worker_id,
                        limit=VIEW_CONCURRENCY,
                        lease_seconds=180,
                        only_view=True,
                    )
                health.mark("view", healthy=True)
                if events:
                    await process_view_batch(events, worker_id, settings.max_attempts)
                else:
                    try:
                        await asyncio.wait_for(stopping.wait(), timeout=settings.worker_poll_seconds)
                    except TimeoutError:
                        pass
            except Exception as exc:
                health.mark("view", healthy=False)
                logger.warning("View queue dependency/retry class=%s", type(exc).__name__)
                await asyncio.sleep(2)
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        server.shutdown()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    start_http_server(9090)
    asyncio.run(serve())
