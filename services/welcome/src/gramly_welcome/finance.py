from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    FinancialLedgerEntry,
    ManagedBot,
    Owner,
    Payment,
    Plan,
    ReferralAttribution,
    ReferralCode,
    Subscription,
    SubscriptionReminder,
    Withdrawal,
)

MONEY = Decimal("0.01")
MINIMUM_WITHDRAWAL_RUB = Decimal("1000.00")


class FinanceError(ValueError):
    pass


@dataclass(frozen=True)
class SettlementResult:
    payment_id: int
    duplicate: bool
    commission_rub: Decimal


def commission_rate(active_referrals: int) -> Decimal:
    if active_referrals >= 21:
        return Decimal("35")
    if active_referrals >= 6:
        return Decimal("25")
    if active_referrals >= 1:
        return Decimal("15")
    return Decimal("0")


def add_calendar_year(value: datetime) -> datetime:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        return value.replace(year=value.year + 1, month=2, day=28)


async def ensure_referral_code(session: AsyncSession, owner_id: int) -> ReferralCode:
    existing = await session.scalar(select(ReferralCode).where(ReferralCode.owner_id == owner_id))
    if existing is not None:
        return existing
    for _ in range(5):
        code = secrets.token_urlsafe(9).replace("-", "").replace("_", "")
        code_id = await session.scalar(
            insert(ReferralCode)
            .values(owner_id=owner_id, code=code)
            .on_conflict_do_nothing()
            .returning(ReferralCode.id)
        )
        if code_id is not None:
            await session.commit()
            result = await session.get(ReferralCode, code_id)
            if result is not None:
                return result
    raise RuntimeError("Could not allocate a referral code")


async def record_first_touch(
    session: AsyncSession, referred_owner_id: int, code: str
) -> ReferralAttribution | None:
    normalized = code.strip()
    referral_code = await session.scalar(
        select(ReferralCode).where(ReferralCode.code == normalized, ReferralCode.is_active.is_(True))
    )
    if referral_code is None or referral_code.owner_id == referred_owner_id:
        return None
    attribution_id = await session.scalar(
        insert(ReferralAttribution)
        .values(
            referrer_owner_id=referral_code.owner_id,
            referred_owner_id=referred_owner_id,
            code_snapshot=referral_code.code,
            status="candidate",
        )
        .on_conflict_do_nothing(constraint="referral_attribution_referred_owner_id_key")
        .returning(ReferralAttribution.id)
    )
    await session.commit()
    if attribution_id is None:
        return cast(
            ReferralAttribution | None,
            await session.scalar(
                select(ReferralAttribution).where(
                    ReferralAttribution.referred_owner_id == referred_owner_id
                )
            ),
        )
    return await session.get(ReferralAttribution, attribution_id)


async def create_payment(
    session: AsyncSession,
    *,
    owner_id: int,
    plan: Plan,
    provider: str,
    amount_rub: Decimal,
    original_amount: Decimal | None = None,
    original_currency: str = "RUB",
) -> Payment:
    if plan.slug != "business" or amount_rub <= 0:
        raise FinanceError("Business plan price is not configured")
    payment = Payment(
        checkout_token=uuid.uuid4(),
        owner_id=owner_id,
        plan_id=plan.id,
        provider=provider,
        status="created",
        amount_rub=amount_rub.quantize(MONEY),
        original_amount=original_amount,
        original_currency=original_currency,
        period_days=30,
    )
    session.add(payment)
    await session.flush()
    return payment


async def _active_referral_count(
    session: AsyncSession, referrer_owner_id: int, now: datetime
) -> int:
    return int(
        await session.scalar(
            select(func.count(ReferralAttribution.id))
            .join(Subscription, Subscription.owner_id == ReferralAttribution.referred_owner_id)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                ReferralAttribution.referrer_owner_id == referrer_owner_id,
                ReferralAttribution.status == "active",
                Subscription.status == "active",
                Subscription.ends_at > now,
                Plan.slug == "business",
            )
        )
        or 0
    )


async def settle_payment(
    session: AsyncSession,
    *,
    payment_id: int,
    provider_invoice_id: str,
    paid_at: datetime,
    original_amount: Decimal,
    original_currency: str,
    paid_asset: str | None,
    exchange_rate_rub: Decimal | None,
    verified_payload: dict[str, object] | None = None,
) -> SettlementResult:
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise FinanceError("Unknown payment")
    if payment.provider_invoice_id not in (None, provider_invoice_id):
        raise FinanceError("Invoice does not belong to this checkout")
    if payment.status == "paid":
        return SettlementResult(payment.id, True, Decimal("0.00"))
    if payment.status != "created":
        raise FinanceError("Payment is not payable")

    plan = await session.get(Plan, payment.plan_id)
    if plan is None or plan.slug != "business":
        raise FinanceError("Business plan is unavailable")
    subscription = await session.scalar(
        select(Subscription).where(Subscription.owner_id == payment.owner_id).with_for_update()
    )
    starts_at = paid_at
    if subscription is not None and subscription.ends_at is not None and subscription.ends_at > paid_at:
        starts_at = subscription.ends_at
    ends_at = starts_at + timedelta(days=payment.period_days)
    if subscription is None:
        subscription = Subscription(
            owner_id=payment.owner_id,
            plan_id=plan.id,
            source=payment.provider,
            status="active",
            starts_at=paid_at,
            ends_at=ends_at,
            auto_renew=payment.provider == "telegram_stars",
            external_reference=provider_invoice_id,
        )
        session.add(subscription)
    else:
        subscription.plan_id = plan.id
        subscription.source = payment.provider
        subscription.status = "active"
        subscription.starts_at = paid_at
        subscription.ends_at = ends_at
        subscription.auto_renew = payment.provider == "telegram_stars"
        subscription.external_reference = provider_invoice_id
    await session.flush()
    for days_before in (7, 3, 1):
        await session.execute(
            insert(SubscriptionReminder)
            .values(
                subscription_id=subscription.id,
                days_before=days_before,
                due_at=ends_at - timedelta(days=days_before),
                status="pending",
                attempts=0,
                last_error="",
                sent_at=None,
            )
            .on_conflict_do_update(
                constraint="uq_subscription_reminder_day",
                set_={
                    "due_at": ends_at - timedelta(days=days_before),
                    "status": "pending",
                    "attempts": 0,
                    "last_error": "",
                    "sent_at": None,
                },
            )
        )

    payment.provider_invoice_id = provider_invoice_id
    payment.status = "paid"
    payment.original_amount = original_amount
    payment.original_currency = original_currency[:16]
    payment.paid_asset = paid_asset[:16] if paid_asset else None
    payment.exchange_rate_rub = exchange_rate_rub
    payment.provider_payload = verified_payload or {}
    payment.paid_at = paid_at

    commission = Decimal("0.00")
    attribution = await session.scalar(
        select(ReferralAttribution)
        .where(ReferralAttribution.referred_owner_id == payment.owner_id)
        .with_for_update()
    )
    has_bot = bool(
        await session.scalar(
            select(ManagedBot.id).where(
                ManagedBot.owner_id == payment.owner_id,
                ManagedBot.is_active.is_(True),
                ManagedBot.webhook_configured.is_(True),
            )
        )
    )
    if attribution is not None and has_bot:
        await session.execute(
            select(Owner).where(Owner.id == attribution.referrer_owner_id).with_for_update()
        )
        if attribution.first_paid_at is None:
            attribution.first_paid_at = paid_at
            attribution.commission_ends_at = add_calendar_year(paid_at)
        attribution.status = "active"
        if attribution.commission_ends_at is not None and paid_at < attribution.commission_ends_at:
            active = await _active_referral_count(session, attribution.referrer_owner_id, paid_at)
            rate = commission_rate(max(1, active))
            base = plan.referral_base_rub or payment.amount_rub
            commission = (base * rate / Decimal("100")).quantize(MONEY, ROUND_HALF_UP)
            if commission > 0:
                session.add(
                    FinancialLedgerEntry(
                        owner_id=attribution.referrer_owner_id,
                        payment_id=payment.id,
                        entry_type="commission",
                        amount_rub=commission,
                        rate_percent=rate,
                        reason="Referral subscription payment",
                        metadata_json={"referred_owner_id": payment.owner_id},
                    )
                )
    await session.commit()
    return SettlementResult(payment.id, False, commission)


async def available_balance(session: AsyncSession, owner_id: int) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(FinancialLedgerEntry.amount_rub), 0)).where(
            FinancialLedgerEntry.owner_id == owner_id
        )
    )
    return Decimal(value or 0).quantize(MONEY)


async def request_withdrawal(
    session: AsyncSession, owner_id: int, amount_rub: Decimal, recipient_telegram_id: int
) -> Withdrawal:
    amount = amount_rub.quantize(MONEY)
    if amount < MINIMUM_WITHDRAWAL_RUB:
        raise FinanceError("Minimum withdrawal is 1000 RUB")
    await session.execute(select(Owner).where(Owner.id == owner_id).with_for_update())
    if await available_balance(session, owner_id) < amount:
        raise FinanceError("Insufficient referral balance")
    public_id = uuid.uuid4()
    withdrawal = Withdrawal(
        public_id=public_id,
        owner_id=owner_id,
        requested_rub=amount,
        recipient_telegram_id=recipient_telegram_id,
        spend_id=f"gramly-{public_id.hex}",
        status="requested",
    )
    session.add(withdrawal)
    await session.flush()
    session.add(
        FinancialLedgerEntry(
            owner_id=owner_id,
            withdrawal_id=withdrawal.id,
            entry_type="reserve",
            amount_rub=-amount,
            reason="Withdrawal reservation",
        )
    )
    await session.commit()
    return withdrawal


async def reject_withdrawal(
    session: AsyncSession, withdrawal_id: int, reason: str
) -> Withdrawal:
    withdrawal = await session.scalar(
        select(Withdrawal).where(Withdrawal.id == withdrawal_id).with_for_update()
    )
    if withdrawal is None or withdrawal.status not in {"requested", "retry", "failed"}:
        raise FinanceError("Withdrawal cannot be rejected")
    withdrawal.status = "rejected"
    withdrawal.rejection_reason = reason[:500]
    withdrawal.processed_at = datetime.now(UTC)
    session.add(
        FinancialLedgerEntry(
            owner_id=withdrawal.owner_id,
            withdrawal_id=withdrawal.id,
            entry_type="reserve_release",
            amount_rub=withdrawal.requested_rub,
            reason=f"Withdrawal rejected: {reason}"[:500],
        )
    )
    await session.commit()
    return withdrawal


async def approve_withdrawal(session: AsyncSession, withdrawal_id: int) -> Withdrawal:
    withdrawal = await session.scalar(
        select(Withdrawal).where(Withdrawal.id == withdrawal_id).with_for_update()
    )
    if withdrawal is None or withdrawal.status != "requested":
        raise FinanceError("Withdrawal cannot be approved")
    withdrawal.status = "processing"
    withdrawal.available_at = datetime.now(UTC)
    await session.commit()
    return withdrawal


async def claim_withdrawal(session: AsyncSession) -> Withdrawal | None:
    now = datetime.now(UTC)
    withdrawal = await session.scalar(
        select(Withdrawal)
        .where(
            Withdrawal.status.in_(("processing", "retry")),
            Withdrawal.available_at <= now,
        )
        .order_by(Withdrawal.available_at, Withdrawal.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if withdrawal is None:
        return None
    withdrawal.status = "processing"
    withdrawal.attempts += 1
    await session.commit()
    return withdrawal


async def retry_withdrawal(
    session: AsyncSession, withdrawal_id: int, error: str, *, max_attempts: int = 8
) -> None:
    withdrawal = await session.scalar(
        select(Withdrawal).where(Withdrawal.id == withdrawal_id).with_for_update()
    )
    if withdrawal is None or withdrawal.status not in {"processing", "retry"}:
        return
    withdrawal.last_error = error[:500]
    if withdrawal.attempts >= max_attempts:
        withdrawal.status = "failed"
        withdrawal.processed_at = datetime.now(UTC)
        session.add(
            FinancialLedgerEntry(
                owner_id=withdrawal.owner_id,
                withdrawal_id=withdrawal.id,
                entry_type="reserve_release",
                amount_rub=withdrawal.requested_rub,
                reason="Withdrawal failed permanently; reserve returned",
            )
        )
    else:
        withdrawal.status = "retry"
        withdrawal.available_at = datetime.now(UTC) + timedelta(
            seconds=min(3600, 15 * (2 ** max(0, withdrawal.attempts - 1)))
        )
    await session.commit()


async def complete_withdrawal(
    session: AsyncSession,
    withdrawal_id: int,
    *,
    payout_amount: Decimal,
    exchange_rate_rub: Decimal,
    provider_transfer_id: str,
) -> Withdrawal:
    withdrawal = await session.scalar(
        select(Withdrawal).where(Withdrawal.id == withdrawal_id).with_for_update()
    )
    if withdrawal is None:
        raise FinanceError("Unknown withdrawal")
    if withdrawal.status == "paid":
        return withdrawal
    if withdrawal.status not in {"requested", "processing", "retry"}:
        raise FinanceError("Withdrawal cannot be paid")
    withdrawal.status = "paid"
    withdrawal.payout_amount = payout_amount
    withdrawal.exchange_rate_rub = exchange_rate_rub
    withdrawal.provider_transfer_id = provider_transfer_id[:128]
    withdrawal.processed_at = datetime.now(UTC)
    session.add_all(
        [
            FinancialLedgerEntry(
                owner_id=withdrawal.owner_id,
                withdrawal_id=withdrawal.id,
                entry_type="reserve_release",
                amount_rub=withdrawal.requested_rub,
                reason="Withdrawal reserve settled",
            ),
            FinancialLedgerEntry(
                owner_id=withdrawal.owner_id,
                withdrawal_id=withdrawal.id,
                entry_type="payout",
                amount_rub=-withdrawal.requested_rub,
                reason=f"Crypto Pay transfer {provider_transfer_id}"[:500],
            ),
        ]
    )
    await session.commit()
    return withdrawal


async def refund_payment(
    session: AsyncSession, payment_id: int, *, reason: str, refunded_at: datetime | None = None
) -> Payment:
    payment = await session.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    if payment is None or payment.status not in {"paid", "refunded"}:
        raise FinanceError("Only a paid payment can be refunded")
    if payment.status == "refunded":
        return payment
    commission = await session.scalar(
        select(FinancialLedgerEntry).where(
            FinancialLedgerEntry.payment_id == payment.id,
            FinancialLedgerEntry.entry_type == "commission",
        )
    )
    if commission is not None:
        session.add(
            FinancialLedgerEntry(
                owner_id=commission.owner_id,
                payment_id=payment.id,
                entry_type="refund",
                amount_rub=-commission.amount_rub,
                rate_percent=commission.rate_percent,
                reason=f"Commission reversal: {reason}"[:500],
            )
        )
    payment.status = "refunded"
    payment.refunded_at = refunded_at or datetime.now(UTC)
    await session.commit()
    return payment


async def add_admin_adjustment(
    session: AsyncSession, owner_id: int, amount_rub: Decimal, *, reason: str
) -> FinancialLedgerEntry:
    amount = amount_rub.quantize(MONEY)
    if amount == 0 or not reason.strip():
        raise FinanceError("Adjustment amount and reason are required")
    await session.execute(select(Owner).where(Owner.id == owner_id).with_for_update())
    entry = FinancialLedgerEntry(
        owner_id=owner_id,
        entry_type="adjustment",
        amount_rub=amount,
        reason=reason.strip()[:500],
    )
    session.add(entry)
    await session.commit()
    return entry
