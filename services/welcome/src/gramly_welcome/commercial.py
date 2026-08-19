from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import FeatureFlag, Owner, Plan, Subscription


@dataclass(frozen=True)
class AccessSnapshot:
    entitled: bool
    status: str
    plan_slug: str | None
    plan_name: str | None
    ends_at: datetime | None
    entitlements: dict[str, bool]
    max_bots: int
    max_channels: int
    monthly_delivery_operations: int
    media_storage_bytes: int


def no_access(status: str = "none") -> AccessSnapshot:
    return AccessSnapshot(False, status, None, None, None, {}, 0, 0, 0, 0)


async def access_for_owner(
    session: AsyncSession, owner_id: int, *, now: datetime | None = None
) -> AccessSnapshot:
    current = now or datetime.now(UTC)
    row = (
        await session.execute(
            select(Subscription, Plan)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(Subscription.owner_id == owner_id)
        )
    ).one_or_none()
    if row is None:
        return no_access()
    subscription, plan = row
    entitled = (
        subscription.status in {"trialing", "active"}
        and subscription.starts_at <= current < subscription.ends_at
        and plan.is_active
    )
    status = subscription.status if entitled else "expired"
    return AccessSnapshot(
        entitled=entitled,
        status=status,
        plan_slug=plan.slug,
        plan_name=plan.display_name,
        ends_at=subscription.ends_at,
        entitlements={key: bool(value) for key, value in plan.entitlements.items()},
        max_bots=plan.max_bots,
        max_channels=plan.max_channels,
        monthly_delivery_operations=plan.monthly_delivery_operations,
        media_storage_bytes=plan.media_storage_bytes,
    )


async def start_trial(
    session: AsyncSession,
    owner_id: int,
    *,
    trial_days: int,
    now: datetime | None = None,
) -> AccessSnapshot:
    current = now or datetime.now(UTC)
    owner = await session.scalar(
        select(Owner).where(Owner.id == owner_id).with_for_update(of=Owner)
    )
    if owner is None:
        raise ValueError("Owner does not exist")
    if owner.trial_started_at is not None:
        return await access_for_owner(session, owner_id, now=current)

    existing = await session.scalar(
        select(Subscription).where(Subscription.owner_id == owner_id).with_for_update()
    )
    if existing is not None and existing.status == "active" and existing.ends_at > current:
        return await access_for_owner(session, owner_id, now=current)

    trial_plan = await session.scalar(select(Plan).where(Plan.slug == "trial", Plan.is_active.is_(True)))
    if trial_plan is None:
        raise RuntimeError("Business trial plan is not configured")
    ends_at = current + timedelta(days=trial_days)
    owner.trial_started_at = current
    owner.trial_ends_at = ends_at
    await session.execute(
        insert(Subscription)
        .values(
            owner_id=owner_id,
            plan_id=trial_plan.id,
            source="trial",
            status="trialing",
            starts_at=current,
            ends_at=ends_at,
            auto_renew=False,
        )
        .on_conflict_do_update(
            constraint="subscription_owner_id_key",
            set_={
                "plan_id": trial_plan.id,
                "source": "trial",
                "status": "trialing",
                "starts_at": current,
                "ends_at": ends_at,
                "auto_renew": False,
                "external_reference": "",
                "updated_at": current,
            },
        )
    )
    await session.commit()
    return await access_for_owner(session, owner_id, now=current)


async def feature_flag_enabled(session: AsyncSession, key: str) -> bool:
    value = await session.scalar(select(FeatureFlag.enabled).where(FeatureFlag.key == key))
    return bool(value)


def payment_method_ready(plan: Plan, method: str) -> bool:
    if method == "crypto_pay":
        return bool(plan.crypto_pay_enabled and plan.price_rub is not None and plan.price_rub > 0)
    if method == "telegram_stars":
        return bool(
            plan.stars_enabled
            and plan.price_xtr is not None
            and plan.price_xtr > 0
            and plan.referral_base_rub is not None
            and plan.referral_base_rub > Decimal("0")
        )
    raise ValueError("Unknown payment method")


def public_plan_payload(plan: Plan) -> dict[str, Any]:
    return {
        "slug": plan.slug,
        "name": plan.display_name,
        "entitlements": plan.entitlements,
        "quotas": {
            "bots": plan.max_bots,
            "channels": plan.max_channels,
            "monthly_delivery_operations": plan.monthly_delivery_operations,
            "media_storage_bytes": plan.media_storage_bytes,
        },
        "prices": {
            "rub": str(plan.price_rub) if payment_method_ready(plan, "crypto_pay") else None,
            "xtr": plan.price_xtr if payment_method_ready(plan, "telegram_stars") else None,
        },
    }


async def list_sellable_plans(session: AsyncSession) -> list[dict[str, Any]]:
    plans = list(
        (
            await session.scalars(
                select(Plan)
                .where(Plan.is_active.is_(True), Plan.is_sellable.is_(True))
                .order_by(Plan.id)
            )
        ).all()
    )
    return [public_plan_payload(plan) for plan in plans]
