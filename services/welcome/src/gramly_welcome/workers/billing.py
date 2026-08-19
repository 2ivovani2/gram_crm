from __future__ import annotations

import asyncio
import logging
import signal
from decimal import ROUND_DOWN, Decimal

from aiogram.exceptions import TelegramAPIError
from prometheus_client import start_http_server

from ..billing import claim_due_reminder, finish_reminder
from ..config import get_settings
from ..crypto_pay import CryptoPayClient, CryptoPayError
from ..db import session_factory
from ..finance import claim_withdrawal, complete_withdrawal, retry_withdrawal
from ..metrics import BILLING_OPERATIONS, WORKER_ACTIVE
from ..owner_bot import interface_bot

logger = logging.getLogger(__name__)


async def process_one() -> bool:
    settings = get_settings()
    async with session_factory() as session:
        withdrawal = await claim_withdrawal(session)
    if withdrawal is None:
        return False
    client = CryptoPayClient(settings.crypto_pay_api_token, settings.crypto_pay_api_base_url)
    try:
        rate = await client.exchange_rate("USDT", "RUB")
        amount = (Decimal(withdrawal.requested_rub) / rate).quantize(
            Decimal("0.000001"), rounding=ROUND_DOWN
        )
        transfer = await client.transfer(
            user_id=withdrawal.recipient_telegram_id,
            amount=amount,
            spend_id=withdrawal.spend_id,
            comment="GramlyHello partner payout",
        )
        async with session_factory() as session:
            await complete_withdrawal(
                session,
                withdrawal.id,
                payout_amount=amount,
                exchange_rate_rub=rate,
                provider_transfer_id=str(transfer.get("transfer_id", "")),
            )
        BILLING_OPERATIONS.labels("withdrawal", "completed").inc()
    except (CryptoPayError, ArithmeticError, ValueError) as exc:
        logger.warning("Withdrawal retry id=%s", withdrawal.id, exc_info=True)
        try:
            reconciled_transfer = await client.get_transfer(withdrawal.spend_id)
        except CryptoPayError:
            reconciled_transfer = None
        if reconciled_transfer is not None and str(reconciled_transfer.get("status")) == "completed":
            amount = Decimal(str(reconciled_transfer["amount"]))
            rate = Decimal(withdrawal.requested_rub) / amount
            async with session_factory() as session:
                await complete_withdrawal(
                    session,
                    withdrawal.id,
                    payout_amount=amount,
                    exchange_rate_rub=rate,
                    provider_transfer_id=str(reconciled_transfer.get("transfer_id", "")),
                )
            BILLING_OPERATIONS.labels("withdrawal", "reconciled").inc()
        else:
            async with session_factory() as session:
                await retry_withdrawal(session, withdrawal.id, str(exc))
            BILLING_OPERATIONS.labels("withdrawal", "retry").inc()
    return True


async def process_one_reminder() -> bool:
    async with session_factory() as session:
        claimed = await claim_due_reminder(session)
    if claimed is None:
        return False
    reminder, telegram_id = claimed
    error: str | None = None
    try:
        await interface_bot().send_message(
            telegram_id,
            "⏳ Подписка GramlyHello Business закончится через "
            f"{reminder.days_before} дн. Продлите её в разделе «💎 ПОДПИСКА».",
        )
    except TelegramAPIError as exc:
        error = str(exc)
    async with session_factory() as session:
        await finish_reminder(session, reminder.id, error=error)
    BILLING_OPERATIONS.labels("renewal_reminder", "retry" if error else "sent").inc()
    return True


async def worker_loop() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Gramly Welcome billing worker")
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event_signal, stopping.set)
    WORKER_ACTIVE.labels("billing").inc()
    try:
        while not stopping.is_set():
            handled = await process_one()
            reminded = await process_one_reminder()
            if handled or reminded:
                continue
            try:
                await asyncio.wait_for(stopping.wait(), timeout=1)
            except TimeoutError:
                pass
    finally:
        WORKER_ACTIVE.labels("billing").dec()


def run() -> None:
    start_http_server(9090)
    asyncio.run(worker_loop())
