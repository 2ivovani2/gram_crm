from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    CampaignAttribution,
    CampaignPayment,
    CampaignTouch,
    MarketingCampaign,
    Owner,
    Payment,
)


def campaign_accepts_touch(campaign: MarketingCampaign, at: datetime) -> bool:
    return bool(
        campaign.is_active
        and campaign.starts_at <= at
        and (campaign.ends_at is None or campaign.ends_at > at)
    )


async def record_campaign_touch(
    session: AsyncSession,
    owner_id: int,
    token: str,
    *,
    at: datetime | None = None,
) -> CampaignTouch | None:
    now = at or datetime.now(UTC)
    try:
        import uuid

        public_token = uuid.UUID(token)
    except (ValueError, TypeError):
        return None
    campaign = await session.scalar(
        select(MarketingCampaign).where(MarketingCampaign.public_token == public_token)
    )
    if campaign is None or not campaign_accepts_touch(campaign, now):
        return None
    owner = await session.get(Owner, owner_id)
    if owner is None:
        return None
    is_new = owner.created_at is not None and owner.created_at >= now - timedelta(minutes=5)
    touch_id = await session.scalar(
        insert(CampaignTouch)
        .values(
            campaign_id=campaign.id,
            owner_id=owner_id,
            click_count=1,
            is_new_free=is_new,
            first_clicked_at=now,
            last_clicked_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_campaign_touch_owner",
            set_={
                "click_count": CampaignTouch.click_count + 1,
                "is_new_free": CampaignTouch.is_new_free | is_new,
                "last_clicked_at": now,
            },
        )
        .returning(CampaignTouch.id)
    )
    await session.commit()
    return await session.get(CampaignTouch, touch_id) if touch_id is not None else None


async def attribute_campaign_payment(
    session: AsyncSession,
    payment: Payment,
    *,
    paid_at: datetime,
) -> int | None:
    attribution = await session.scalar(
        select(CampaignAttribution)
        .where(CampaignAttribution.owner_id == payment.owner_id)
        .with_for_update()
    )
    if attribution is None:
        candidates = (
            await session.execute(
                select(CampaignTouch, MarketingCampaign)
                .join(MarketingCampaign, MarketingCampaign.id == CampaignTouch.campaign_id)
                .where(
                    CampaignTouch.owner_id == payment.owner_id,
                    CampaignTouch.last_clicked_at <= paid_at,
                    CampaignTouch.last_clicked_at >= paid_at - timedelta(days=90),
                )
                .order_by(CampaignTouch.last_clicked_at.desc())
            )
        ).all()
        selected = next(
            (
                (touch, campaign)
                for touch, campaign in candidates
                if touch.last_clicked_at + timedelta(days=campaign.attribution_window_days) >= paid_at
            ),
            None,
        )
        if selected is None:
            return None
        touch, campaign = selected
        attribution = CampaignAttribution(
            campaign_id=campaign.id,
            owner_id=payment.owner_id,
            attributed_at=paid_at,
        )
        session.add(attribution)
        await session.flush()
    await session.execute(
        insert(CampaignPayment)
        .values(
            campaign_id=attribution.campaign_id,
            owner_id=payment.owner_id,
            payment_id=payment.id,
            amount_rub=Decimal(payment.amount_rub),
            paid_at=paid_at,
        )
        .on_conflict_do_nothing(constraint="campaign_payment_payment_id_key")
    )
    return attribution.campaign_id
