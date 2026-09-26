"""Bounded external work with an actively renewed database lease."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update

from ..db import session_factory


async def run_leased(
    work: Awaitable[None], model: Any, row_id: int, worker_id: str, *, timeout_seconds: int = 120
) -> None:
    async def renew() -> None:
        while True:
            await asyncio.sleep(20)
            async with session_factory() as session:
                renewed = await session.scalar(update(model).where(
                    model.id == row_id, model.lease_owner == worker_id, model.status == "processing",
                ).values(lease_expires_at=datetime.now(UTC) + timedelta(seconds=180)).returning(model.id))
                await session.commit()
                if renewed is None:
                    raise RuntimeError("Delivery lease no longer owned")

    action, renewal = asyncio.ensure_future(work), asyncio.create_task(renew())
    try:
        async with asyncio.timeout(timeout_seconds):
            done, _ = await asyncio.wait((action, renewal), return_when=asyncio.FIRST_COMPLETED)
            if renewal in done:
                await renewal
            await action
    finally:
        action.cancel()
        renewal.cancel()
        await asyncio.gather(action, renewal, return_exceptions=True)
