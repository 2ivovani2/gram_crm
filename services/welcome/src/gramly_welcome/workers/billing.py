from __future__ import annotations

import asyncio
import logging
from decimal import ROUND_DOWN, Decimal

from aiogram.exceptions import TelegramAPIError

from ..billing import claim_due_reminder, finish_reminder
from ..config import get_settings
from ..crypto_pay import CryptoPayClient, CryptoPayError
from ..db import session_factory
from ..finance import claim_withdrawal, complete_withdrawal, retry_withdrawal
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
        else:
            async with session_factory() as session:
                await retry_withdrawal(session, withdrawal.id, str(exc))
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
    return True


async def worker_loop() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Gramly Welcome billing worker")
    while True:
        handled = await process_one()
        reminded = await process_one_reminder()
        if not handled and not reminded:
            await asyncio.sleep(1)


def run() -> None:
    asyncio.run(worker_loop())
