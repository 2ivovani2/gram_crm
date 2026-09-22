from __future__ import annotations

import asyncio
import logging
import signal
import socket
import uuid
from decimal import ROUND_DOWN, Decimal

from aiogram.exceptions import TelegramAPIError
from prometheus_client import start_http_server

from ..billing import (
    claim_due_reminder,
    claim_payment_event,
    complete_payment_event,
    expire_one_business_subscription,
    finish_reminder,
    purge_one_terminal_payment,
    reconcile_one_expired_payment,
    retry_payment_event,
    verify_and_settle_crypto_invoice,
)
from ..config import get_settings
from ..crypto_pay import CryptoPayClient, CryptoPayError
from ..db import session_factory
from ..finance import FinanceError, claim_withdrawal, complete_withdrawal, retry_withdrawal
from ..metrics import BILLING_OPERATIONS, PAYMENT_EVENTS, WORKER_ACTIVE
from ..owner_bot import interface_bot

logger = logging.getLogger(__name__)


async def process_one_payment_event(worker_id: str) -> bool:
    settings = get_settings()
    async with session_factory() as session:
        event = await claim_payment_event(session, worker_id=worker_id)
    if event is None:
        return False
    client = CryptoPayClient(settings.crypto_pay_api_token, settings.crypto_pay_api_base_url)
    try:
        invoice = await client.get_invoice(event.provider_reference)
        async with session_factory() as session:
            await verify_and_settle_crypto_invoice(
                session, client, event.provider_reference, invoice=invoice
            )
            await complete_payment_event(
                session, provider=event.provider, event_key=event.event_key
            )
    except (CryptoPayError, FinanceError) as exc:
        async with session_factory() as session:
            await retry_payment_event(
                session, event.id, worker_id=worker_id, error=type(exc).__name__
            )
        PAYMENT_EVENTS.labels("crypto_pay", "retry").inc()
    else:
        PAYMENT_EVENTS.labels("crypto_pay", "settled").inc()
    return True


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
    reminder, telegram_id, bot_names = claimed
    error: str | None = None
    try:
        affected = ", ".join(bot_names) if bot_names else "подключённые боты"
        await interface_bot().send_message(
            telegram_id,
            "⏳ Подписка GramlyHello Business закончится через "
            f"{reminder.days_before} дн.\n\n"
            f"Затронуто: {affected}. После окончания приветствия продолжат работать на Free, "
            "но в них вернётся реклама, а ротация будет приостановлена. "
            "Продлите Business в разделе «💎 ПОДПИСКА».",
        )
    except TelegramAPIError as exc:
        error = str(exc)
    async with session_factory() as session:
        await finish_reminder(session, reminder.id, error=error)
    BILLING_OPERATIONS.labels("renewal_reminder", "retry" if error else "sent").inc()
    return True


async def process_one_expired_payment() -> bool:
    settings = get_settings()
    client = (
        CryptoPayClient(settings.crypto_pay_api_token, settings.crypto_pay_api_base_url)
        if settings.crypto_pay_api_token
        else None
    )
    async with session_factory() as session:
        result = await reconcile_one_expired_payment(session, client)
    if result is None:
        return False
    provider, outcome = result
    BILLING_OPERATIONS.labels(f"payment_expiry_{provider}", outcome).inc()
    return True


async def purge_one_expired_payment() -> bool:
    async with session_factory() as session:
        provider = await purge_one_terminal_payment(session)
    if provider is None:
        return False
    BILLING_OPERATIONS.labels(f"payment_cleanup_{provider}", "deleted").inc()
    return True


async def process_one_expired_subscription() -> bool:
    async with session_factory() as session:
        expired = await expire_one_business_subscription(session)
    if expired:
        BILLING_OPERATIONS.labels("subscription_expiry", "downgraded").inc()
    return expired


async def worker_loop() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Gramly Welcome billing worker")
    worker_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event_signal, stopping.set)
    WORKER_ACTIVE.labels("billing").inc()
    try:
        while not stopping.is_set():
            payment_event = await process_one_payment_event(worker_id)
            handled = await process_one()
            reminded = await process_one_reminder()
            expired = await process_one_expired_payment()
            purged = await purge_one_expired_payment()
            downgraded = await process_one_expired_subscription()
            if payment_event or handled or reminded or expired or purged or downgraded:
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
