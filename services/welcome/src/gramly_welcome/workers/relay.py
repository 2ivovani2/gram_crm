from __future__ import annotations

import asyncio
import logging
import signal

from prometheus_client import start_http_server
from redis.asyncio import Redis

from ..config import get_settings
from ..db import session_factory
from ..delivery_streams import (
    claim_jobs_for_signal,
    ensure_consumer_groups,
    publish_jobs,
    recover_unsignaled_jobs,
)
from ..metrics import WORKER_ACTIVE
from .health import WorkerHealth

logger = logging.getLogger(__name__)


async def serve() -> None:
    settings = get_settings()
    if not settings.delivery_streams_enabled:
        raise RuntimeError("Delivery Streams relay requires WELCOME_DELIVERY_STREAMS_ENABLED=true")
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event_signal, stopping.set)
    redis = Redis.from_url(
        settings.valkey_url,
        decode_responses=False,
        socket_timeout=0.2,
        socket_connect_timeout=0.2,
        health_check_interval=15,
    )
    health = WorkerHealth({"relay": 30})
    server = health.start()
    WORKER_ACTIVE.labels("delivery-relay").inc()
    try:
        await ensure_consumer_groups(redis, settings.delivery_stream_prefix)
        while not stopping.is_set():
            health.mark("relay", healthy=True)
            async with session_factory() as session:
                await recover_unsignaled_jobs(session)
                jobs = await claim_jobs_for_signal(
                    session, limit=settings.delivery_relay_batch_size
                )
            if jobs:
                async with session_factory() as session:
                    await publish_jobs(
                        redis, session, jobs, prefix=settings.delivery_stream_prefix
                    )
                continue
            try:
                await asyncio.wait_for(stopping.wait(), timeout=settings.worker_poll_seconds)
            except TimeoutError:
                pass
    finally:
        WORKER_ACTIVE.labels("delivery-relay").dec()
        server.shutdown()
        await redis.aclose()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    start_http_server(9090)
    asyncio.run(serve())


if __name__ == "__main__":
    run()
