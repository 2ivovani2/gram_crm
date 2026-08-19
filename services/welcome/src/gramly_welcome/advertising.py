from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .commercial import access_for_owner
from .metrics import AD_CLICKS, AD_DELIVERIES
from .models import AdCreative, AdImpression


@dataclass(frozen=True)
class ScheduledAd:
    impression: AdImpression
    payload: dict[str, Any]


@dataclass(frozen=True)
class AdCreativeInput:
    name: str
    text: str
    entities: list[dict[str, Any]]
    cta_text: str = ""
    cta_url: str = ""
    weight: int = 1


def validate_ad_creative(value: AdCreativeInput) -> None:
    if not value.name.strip() or len(value.name.strip()) > 128:
        raise ValueError("Advertising name must contain 1 to 128 characters")
    if not value.text.strip() or len(value.text) > 4096:
        raise ValueError("Advertising text must contain 1 to 4096 characters")
    if not 1 <= value.weight <= 1000:
        raise ValueError("Advertising weight must be between 1 and 1000")
    if bool(value.cta_text) != bool(value.cta_url):
        raise ValueError("CTA text and URL must be configured together")
    if value.cta_url:
        parsed = urlsplit(value.cta_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Advertising CTA must use an absolute HTTP(S) URL")


async def create_ad_creative(
    session: AsyncSession,
    value: AdCreativeInput,
    *,
    administrator_telegram_id: int,
) -> AdCreative:
    validate_ad_creative(value)
    creative = AdCreative(
        name=value.name.strip(),
        text=value.text,
        entities=value.entities,
        cta_text=value.cta_text.strip(),
        cta_url=value.cta_url.strip(),
        weight=value.weight,
        is_active=True,
        created_by_telegram_id=administrator_telegram_id,
    )
    session.add(creative)
    await session.commit()
    return creative


async def set_ad_creative_active(session: AsyncSession, creative_id: int, *, active: bool) -> None:
    creative = await session.get(AdCreative, creative_id)
    if creative is None:
        raise ValueError("Advertising creative was not found")
    if not active and creative.is_active:
        active_count = int(
            await session.scalar(select(func.count(AdCreative.id)).where(AdCreative.is_active.is_(True))) or 0
        )
        if active_count <= 1:
            raise ValueError("At least one Free advertising creative must remain active")
    creative.is_active = active
    await session.commit()


def advertising_payload(
    creative: AdCreative, public_token: uuid.UUID, public_service_base_url: str
) -> dict[str, Any]:
    payload: dict[str, Any] = {"text": creative.text, "entities": creative.entities}
    if creative.cta_text and creative.cta_url:
        tracking_url = f"{public_service_base_url.rstrip('/')}/ad/{public_token}"
        payload["keyboard"] = {
            "kind": "inline",
            "rows": [
                [
                    {
                        "text": creative.cta_text,
                        "action_type": "url",
                        "value": tracking_url,
                        "style": "default",
                    }
                ]
            ],
        }
    return payload


async def choose_free_ad(
    session: AsyncSession,
    *,
    owner_id: int,
    bot_id: int,
    channel_id: int,
    contact_id: int,
    flow_delivery_id: int,
    public_service_base_url: str,
) -> ScheduledAd | None:
    access = await access_for_owner(session, owner_id)
    if access.entitlements.get("ad_free", False):
        return None
    creatives = list(
        (
            await session.scalars(
                select(AdCreative).where(AdCreative.is_active.is_(True)).order_by(AdCreative.id)
            )
        ).all()
    )
    if not creatives:
        return None
    creative = secrets.SystemRandom().choices(creatives, weights=[item.weight for item in creatives], k=1)[0]
    public_token = uuid.uuid4()
    impression = AdImpression(
        public_token=public_token,
        creative_id=creative.id,
        owner_id=owner_id,
        bot_id=bot_id,
        channel_id=channel_id,
        contact_id=contact_id,
        flow_delivery_id=flow_delivery_id,
        status="scheduled",
        destination_url=creative.cta_url,
    )
    session.add(impression)
    return ScheduledAd(
        impression=impression,
        payload=advertising_payload(creative, public_token, public_service_base_url),
    )


async def mark_ad_operation(
    session: AsyncSession,
    operation_id: int,
    *,
    status: str,
    error: str = "",
) -> None:
    impression = await session.scalar(
        select(AdImpression).where(AdImpression.operation_id == operation_id).with_for_update()
    )
    if impression is None:
        return
    impression.status = status
    impression.error = error[:500]
    if status == "sent":
        impression.shown_at = datetime.now(UTC)
    AD_DELIVERIES.labels(status).inc()


async def record_ad_click(session: AsyncSession, public_token: uuid.UUID) -> str | None:
    impression = await session.scalar(
        select(AdImpression)
        .where(
            AdImpression.public_token == public_token,
            AdImpression.status == "sent",
            AdImpression.destination_url != "",
        )
        .with_for_update()
    )
    if impression is None:
        return None
    now = datetime.now(UTC)
    impression.click_count += 1
    impression.first_clicked_at = impression.first_clicked_at or now
    impression.last_clicked_at = now
    await session.commit()
    AD_CLICKS.inc()
    return impression.destination_url


async def ad_statistics(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(
                AdCreative.id,
                AdCreative.name,
                AdCreative.is_active,
                func.count(AdImpression.id).filter(AdImpression.status == "sent"),
                func.count(AdImpression.id).filter(AdImpression.status == "failed"),
                func.count(AdImpression.id).filter(AdImpression.first_clicked_at.is_not(None)),
                func.coalesce(func.sum(AdImpression.click_count), 0),
            )
            .outerjoin(AdImpression, AdImpression.creative_id == AdCreative.id)
            .group_by(AdCreative.id)
            .order_by(AdCreative.id)
        )
    ).all()
    return [
        {
            "id": int(row[0]),
            "name": str(row[1]),
            "active": bool(row[2]),
            "impressions": int(row[3]),
            "failures": int(row[4]),
            "unique_clicks": int(row[5]),
            "clicks": int(row[6]),
            "ctr": (float(row[5]) / int(row[3])) if row[3] else 0.0,
        }
        for row in rows
    ]
