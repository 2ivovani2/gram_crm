from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .commercial import feature_flag_enabled, payment_method_ready
from .crypto_pay import CryptoInvoice, CryptoPayClient
from .finance import FinanceError, SettlementResult, create_payment, settle_payment
from .models import Owner, Payment, PaymentEvent, Plan, Subscription, SubscriptionReminder


async def business_plan(session: AsyncSession) -> Plan:
    plan = await session.scalar(select(Plan).where(Plan.slug == "business", Plan.is_active.is_(True)))
    if plan is None:
        raise FinanceError("Business plan is unavailable")
    return plan


async def create_crypto_checkout(
    session: AsyncSession, owner_id: int, client: CryptoPayClient, *, surface: str
) -> Payment:
    flag = f"crypto_pay_{surface}_checkout"
    if not await feature_flag_enabled(session, flag):
        raise FinanceError("Crypto Pay checkout is disabled")
    plan = await business_plan(session)
    if not payment_method_ready(plan, "crypto_pay") or plan.price_rub is None:
        raise FinanceError("Crypto Pay price is not configured")
    payment = await create_payment(
        session,
        owner_id=owner_id,
        plan=plan,
        provider="crypto_pay",
        amount_rub=Decimal(plan.price_rub),
    )
    await session.commit()
    invoice = await client.create_invoice(
        amount_rub=payment.amount_rub,
        payload=str(payment.checkout_token),
        description="GramlyHello Business — 30 days",
    )
    payment.provider_invoice_id = invoice.invoice_id
    payment.invoice_url = (
        invoice.mini_app_invoice_url if surface == "mini_app" else invoice.bot_invoice_url
    )
    payment.provider_payload = {"currency_type": invoice.currency_type, "fiat": invoice.fiat}
    await session.commit()
    return payment


async def create_stars_checkout(session: AsyncSession, owner_id: int) -> Payment:
    if not await feature_flag_enabled(session, "telegram_stars_checkout"):
        raise FinanceError("Telegram Stars checkout is disabled")
    plan = await business_plan(session)
    if not payment_method_ready(plan, "telegram_stars") or plan.referral_base_rub is None:
        raise FinanceError("Telegram Stars price is not configured")
    payment = await create_payment(
        session,
        owner_id=owner_id,
        plan=plan,
        provider="telegram_stars",
        amount_rub=Decimal(plan.referral_base_rub),
        original_amount=Decimal(plan.price_xtr or 0),
        original_currency="XTR",
    )
    await session.commit()
    return payment


def payment_token_from_payload(payload: str) -> uuid.UUID:
    try:
        return uuid.UUID(payload)
    except ValueError as exc:
        raise FinanceError("Invalid checkout payload") from exc


async def verify_and_settle_crypto_invoice(
    session: AsyncSession, client: CryptoPayClient, invoice_id: str
) -> SettlementResult:
    invoice = await client.get_invoice(invoice_id)
    if invoice.status != "paid" or invoice.currency_type != "fiat" or invoice.fiat != "RUB":
        raise FinanceError("Invoice is not a paid RUB invoice")
    token = payment_token_from_payload(invoice.payload)
    payment = await session.scalar(
        select(Payment).where(Payment.checkout_token == token, Payment.provider == "crypto_pay")
    )
    if payment is None or payment.provider_invoice_id != invoice.invoice_id:
        raise FinanceError("Invoice does not match a Gramly checkout")
    if invoice.amount != payment.amount_rub:
        raise FinanceError("Invoice amount mismatch")
    paid_at_raw = invoice.raw.get("paid_at")
    paid_at = (
        datetime.fromisoformat(str(paid_at_raw).replace("Z", "+00:00"))
        if paid_at_raw
        else datetime.now(UTC)
    )
    if paid_at.tzinfo is None:
        paid_at = paid_at.replace(tzinfo=UTC)
    return await settle_payment(
        session,
        payment_id=payment.id,
        provider_invoice_id=invoice.invoice_id,
        paid_at=paid_at,
        original_amount=invoice.paid_amount or invoice.amount,
        original_currency=invoice.paid_asset or "RUB",
        paid_asset=invoice.paid_asset,
        exchange_rate_rub=invoice.paid_fiat_rate,
        verified_payload=safe_invoice_snapshot(invoice),
    )


async def settle_stars_payment(
    session: AsyncSession,
    *,
    payload: str,
    telegram_payment_charge_id: str,
    stars: int,
    paid_at: datetime | None = None,
) -> SettlementResult:
    token = payment_token_from_payload(payload)
    payment = await session.scalar(
        select(Payment).where(Payment.checkout_token == token, Payment.provider == "telegram_stars")
    )
    if payment is None or payment.original_amount != Decimal(stars):
        raise FinanceError("Stars payment does not match checkout")
    settled = await session.scalar(
        select(Payment).where(
            Payment.provider == "telegram_stars",
            Payment.provider_invoice_id == telegram_payment_charge_id,
        )
    )
    if settled is not None:
        return SettlementResult(settled.id, True, Decimal("0.00"))
    if payment.status == "paid":
        plan = await session.get(Plan, payment.plan_id)
        if plan is None:
            raise FinanceError("Business plan is unavailable")
        payment = await create_payment(
            session,
            owner_id=payment.owner_id,
            plan=plan,
            provider="telegram_stars",
            amount_rub=payment.amount_rub,
            original_amount=Decimal(stars),
            original_currency="XTR",
        )
        await session.flush()
    return await settle_payment(
        session,
        payment_id=payment.id,
        provider_invoice_id=telegram_payment_charge_id,
        paid_at=paid_at or datetime.now(UTC),
        original_amount=Decimal(stars),
        original_currency="XTR",
        paid_asset="XTR",
        exchange_rate_rub=(payment.amount_rub / Decimal(stars)) if stars else None,
        verified_payload={"telegram_payment_charge_id": telegram_payment_charge_id},
    )


async def register_payment_event(
    session: AsyncSession, *, provider: str, event_key: str, raw_body: bytes
) -> bool:
    event_id = await session.scalar(
        insert(PaymentEvent)
        .values(
            provider=provider,
            event_key=event_key,
            payload_hash=hashlib.sha256(raw_body).hexdigest(),
            status="received",
        )
        .on_conflict_do_nothing(constraint="uq_payment_event_provider_key")
        .returning(PaymentEvent.id)
    )
    await session.commit()
    return event_id is not None


async def complete_payment_event(
    session: AsyncSession, *, provider: str, event_key: str
) -> None:
    event = await session.scalar(
        select(PaymentEvent).where(
            PaymentEvent.provider == provider, PaymentEvent.event_key == event_key
        )
    )
    if event is not None:
        event.status = "processed"
        event.processed_at = datetime.now(UTC)
        await session.commit()


def safe_invoice_snapshot(invoice: CryptoInvoice) -> dict[str, object]:
    # Keep reconciliation data but intentionally exclude free-form comments and URLs.
    return {
        "invoice_id": invoice.invoice_id,
        "status": invoice.status,
        "amount": str(invoice.amount),
        "fiat": invoice.fiat,
        "paid_asset": invoice.paid_asset,
        "paid_amount": str(invoice.paid_amount) if invoice.paid_amount is not None else None,
        "paid_fiat_rate": (
            str(invoice.paid_fiat_rate) if invoice.paid_fiat_rate is not None else None
        ),
        "snapshot_hash": hashlib.sha256(
            json.dumps(invoice.raw, sort_keys=True, default=str).encode()
        ).hexdigest(),
    }


async def claim_due_reminder(
    session: AsyncSession,
) -> tuple[SubscriptionReminder, int] | None:
    now = datetime.now(UTC)
    reminder = await session.scalar(
        select(SubscriptionReminder)
        .where(
            SubscriptionReminder.status.in_(("pending", "retry")),
            SubscriptionReminder.due_at <= now,
        )
        .order_by(SubscriptionReminder.due_at, SubscriptionReminder.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if reminder is None:
        return None
    telegram_id = await session.scalar(
        select(Owner.telegram_id)
        .join(Subscription, Subscription.owner_id == Owner.id)
        .where(Subscription.id == reminder.subscription_id)
    )
    if telegram_id is None:
        reminder.status = "failed"
        reminder.last_error = "Subscription owner is unavailable"
        await session.commit()
        return None
    reminder.status = "processing"
    reminder.attempts += 1
    await session.commit()
    return reminder, int(telegram_id)


async def finish_reminder(
    session: AsyncSession, reminder_id: int, *, error: str | None = None
) -> None:
    reminder = await session.scalar(
        select(SubscriptionReminder)
        .where(SubscriptionReminder.id == reminder_id)
        .with_for_update()
    )
    if reminder is None or reminder.status != "processing":
        return
    if error is None:
        reminder.status = "sent"
        reminder.sent_at = datetime.now(UTC)
    elif reminder.attempts >= 8:
        reminder.status = "failed"
        reminder.last_error = error[:500]
    else:
        reminder.status = "retry"
        reminder.last_error = error[:500]
        reminder.due_at = datetime.now(UTC) + timedelta(
            seconds=min(3600, 15 * (2 ** max(0, reminder.attempts - 1)))
        )
    await session.commit()
