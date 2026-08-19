from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome.finance import (
    add_admin_adjustment,
    available_balance,
    create_payment,
    record_first_touch,
    reject_withdrawal,
    request_withdrawal,
    settle_payment,
)
from gramly_welcome.models import (
    FinancialLedgerEntry,
    ManagedBot,
    Owner,
    Plan,
    ReferralCode,
    Subscription,
)

DATABASE_URL = os.getenv("WELCOME_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="WELCOME_TEST_DATABASE_URL is not set")


@pytest.mark.asyncio
async def test_payment_referral_replay_withdrawal_and_immutable_ledger() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        referrer = Owner(telegram_id=-12001, username="referrer")
        referred = Owner(telegram_id=-12002, username="referred")
        session.add_all([referrer, referred])
        await session.flush()
        session.add(ReferralCode(owner_id=referrer.id, code="integration-code"))
        plan = await session.scalar(select(Plan).where(Plan.slug == "business"))
        assert plan is not None
        plan.price_rub = Decimal("1000")
        plan.referral_base_rub = Decimal("1000")
        plan.crypto_pay_enabled = True
        session.add(
            ManagedBot(
                owner_id=referred.id,
                public_id=uuid.uuid4(),
                telegram_id=-22002,
                display_name="Integration",
                token_ciphertext="not-a-real-token",
                webhook_secret="webhook",
                path_secret="path",
                is_active=True,
                webhook_configured=True,
            )
        )
        await session.commit()
        assert await record_first_touch(session, referred.id, "integration-code") is not None
        payment = await create_payment(
            session,
            owner_id=referred.id,
            plan=plan,
            provider="crypto_pay",
            amount_rub=Decimal("1000"),
        )
        await session.commit()
        first = await settle_payment(
            session,
            payment_id=payment.id,
            provider_invoice_id="invoice-1",
            paid_at=datetime.now(UTC),
            original_amount=Decimal("10"),
            original_currency="USDT",
            paid_asset="USDT",
            exchange_rate_rub=Decimal("100"),
        )
        replay = await settle_payment(
            session,
            payment_id=payment.id,
            provider_invoice_id="invoice-1",
            paid_at=datetime.now(UTC),
            original_amount=Decimal("10"),
            original_currency="USDT",
            paid_asset="USDT",
            exchange_rate_rub=Decimal("100"),
        )
        assert first.commission_rub == Decimal("150.00")
        assert replay.duplicate
        assert await session.scalar(
            select(func.count(FinancialLedgerEntry.id)).where(
                FinancialLedgerEntry.payment_id == payment.id,
                FinancialLedgerEntry.entry_type == "commission",
            )
        ) == 1
        subscription = await session.scalar(
            select(Subscription).where(Subscription.owner_id == referred.id)
        )
        assert subscription is not None and subscription.status == "active"

        await add_admin_adjustment(session, referrer.id, Decimal("1000"), reason="test credit")
        withdrawal = await request_withdrawal(
            session, referrer.id, Decimal("1000"), referrer.telegram_id
        )
        assert await available_balance(session, referrer.id) == Decimal("150.00")
        await reject_withdrawal(session, withdrawal.id, "integration rejection")
        assert await available_balance(session, referrer.id) == Decimal("1150.00")

        with pytest.raises(DBAPIError):
            await session.execute(
                update(FinancialLedgerEntry)
                .where(FinancialLedgerEntry.payment_id == payment.id)
                .values(amount_rub=Decimal("999"))
            )
            await session.commit()
        await session.rollback()
    await engine.dispose()
