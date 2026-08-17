from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from ..config import get_settings
from ..db import session_factory
from ..metrics import DELIVERY_ATTEMPTS, WORKER_ACTIVE
from ..models import GreetingDelivery
from ..repository import claim_delivery_batch

logger = logging.getLogger(__name__)


async def serve() -> None:
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event_signal, stopping.set)
    WORKER_ACTIVE.labels("delivery").inc()
    try:
        while not stopping.is_set():
            async with session_factory() as session:
                deliveries = await claim_delivery_batch(
                    session,
                    worker_id=worker_id,
                    limit=settings.worker_batch_size,
                    lease_seconds=settings.lease_seconds,
                )
            if not deliveries:
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
                continue
            # Telegram/S3 execution is intentionally activated together with
            # the migrated media plane in the staging MR. Until then this
            # deployment is not routed or enabled in production.
            for delivery in deliveries:
                logger.warning("delivery consumer not enabled before staging cutover id=%s", delivery.id)
                async with session_factory() as session:
                    await session.execute(
                        update(GreetingDelivery)
                        .where(GreetingDelivery.id == delivery.id, GreetingDelivery.lease_owner == worker_id)
                        .values(
                            status="retry",
                            due_at=datetime.now(UTC) + timedelta(seconds=30),
                            lease_owner=None,
                            lease_expires_at=None,
                            error="consumer_disabled",
                        )
                    )
                    await session.commit()
                DELIVERY_ATTEMPTS.labels("deferred").inc()
    finally:
        WORKER_ACTIVE.labels("delivery").dec()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())


if __name__ == "__main__":
    run()
