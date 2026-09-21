"""Re-apply managed-bot webhooks after changing allowed Telegram update types."""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select

from .config import get_settings
from .crypto import TokenKeyring
from .db import session_factory
from .models import ManagedBot
from .owner_bot import configure_customer_webhook

logger = logging.getLogger(__name__)


async def reconcile(bot_id: int | None = None) -> tuple[int, int]:
    settings = get_settings()
    keyring = TokenKeyring.parse(settings.token_encryption_keys)
    async with session_factory() as session:
        query = select(ManagedBot).where(ManagedBot.is_active.is_(True)).order_by(ManagedBot.id)
        if bot_id is not None:
            query = query.where(ManagedBot.id == bot_id)
        bots = list(await session.scalars(query))
    updated = 0
    failed = 0
    for managed in bots:
        try:
            await configure_customer_webhook(
                managed,
                keyring.decrypt(managed.token_ciphertext),
                settings,
            )
        except Exception:
            failed += 1
            logger.exception("managed_bot_webhook_reconcile_failed bot_id=%s", managed.id)
        else:
            updated += 1
    return updated, failed


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot-id", type=int)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    updated, failed = asyncio.run(reconcile(args.bot_id))
    logger.info("managed_bot_webhook_reconcile_complete updated=%s failed=%s", updated, failed)
    print(f"Managed bot webhooks: updated={updated} failed={failed}", flush=True)
    if failed and args.strict:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
