from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Channel, ManagedBot, Owner, Payment, Plan, Subscription, WeeklyBusinessSnapshot

MONEY = Decimal("0.01")


def weekly_revenue_run_rate(calculation_price: Decimal, active_paid_clients: int) -> Decimal:
    return (
        calculation_price * active_paid_clients * Decimal(12) / Decimal(52)
    ).quantize(MONEY, ROUND_HALF_UP)


async def live_business_metrics(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[str, object]:
    current = now or datetime.now(UTC)
    plan = await session.scalar(select(Plan).where(Plan.slug == "business"))
    calculation_price = Decimal(plan.calculation_price_rub or 0) if plan else Decimal("0")
    active_paid = int(
        await session.scalar(
            select(func.count(Subscription.id))
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                Plan.slug == "business",
                Subscription.status == "active",
                Subscription.ends_at > current,
                exists(
                    select(Payment.id).where(
                        Payment.owner_id == Subscription.owner_id,
                        Payment.status == "paid",
                    )
                ),
            )
        )
        or 0
    )
    first_paid = (
        select(Payment.owner_id, func.min(Payment.paid_at).label("first_paid_at"))
        .where(Payment.status == "paid", Payment.paid_at.is_not(None))
        .group_by(Payment.owner_id)
        .subquery()
    )
    first_paid_30d = int(
        await session.scalar(
            select(func.count()).select_from(first_paid).where(
                first_paid.c.first_paid_at >= current - timedelta(days=30)
            )
        )
        or 0
    )
    paid_owners = select(Payment.owner_id).where(Payment.status == "paid").distinct()
    bot_activity = select(func.max(ManagedBot.updated_at)).where(
        ManagedBot.owner_id == Owner.id
    ).correlate(Owner)
    channel_activity = (
        select(func.max(func.coalesce(Channel.disconnected_at, Channel.connected_at)))
        .join(ManagedBot, ManagedBot.id == Channel.bot_id)
        .where(ManagedBot.owner_id == Owner.id)
        .correlate(Owner)
    )
    churned_7d = int(
        await session.scalar(
            select(func.count(Owner.id)).where(
                Owner.id.in_(paid_owners),
                ~exists(
                    select(ManagedBot.id).where(
                        ManagedBot.owner_id == Owner.id,
                        ManagedBot.is_active.is_(True),
                    )
                ),
                ~exists(
                    select(Channel.id)
                    .join(ManagedBot, ManagedBot.id == Channel.bot_id)
                    .where(
                        ManagedBot.owner_id == Owner.id,
                        Channel.is_active.is_(True),
                    )
                ),
                or_(
                    bot_activity.scalar_subquery() >= current - timedelta(days=7),
                    channel_activity.scalar_subquery() >= current - timedelta(days=7),
                ),
            )
        )
        or 0
    )
    wrr = weekly_revenue_run_rate(calculation_price, active_paid)
    return {
        "calculation_price_rub": calculation_price.quantize(MONEY),
        "active_paid_clients": active_paid,
        "first_paid_30d": first_paid_30d,
        "churned_7d": churned_7d,
        "weekly_revenue_run_rate_rub": wrr,
    }


async def ensure_weekly_snapshot(
    session: AsyncSession, *, now: datetime | None = None, force: bool = False
) -> date | None:
    current = now or datetime.now(UTC)
    if current.weekday() != 0 and not force:
        return None
    week_start = current.date() - timedelta(days=current.weekday())
    values = await live_business_metrics(session, now=current)
    await session.execute(
        insert(WeeklyBusinessSnapshot)
        .values(week_start=week_start, **values)
        .on_conflict_do_nothing(constraint="weekly_business_snapshot_week_key")
    )
    await session.commit()
    return week_start
