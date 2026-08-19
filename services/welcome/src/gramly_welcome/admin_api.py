from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import session_dependency
from .models import (
    AdCreative,
    AdImpression,
    Announcement,
    ContextualHelp,
    EventLog,
    FeatureFlag,
    Manual,
    Owner,
    OwnerNotification,
    Plan,
    Tip,
)

router = APIRouter(prefix="/api/admin/v1", tags=["welcome-admin"])
SessionDep = Annotated[AsyncSession, Depends(session_dependency)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
OWNER_GROUPS = {"gramly-owners", "authentik Admins"}


class AdminIdentity(BaseModel):
    username: str
    groups: set[str]


def _groups(raw: str) -> set[str]:
    return {item.strip() for item in raw.replace(",", "|").split("|") if item.strip()}


async def current_admin(
    x_authentik_username: Annotated[str, Header(alias="X-authentik-username")] = "",
    x_authentik_groups: Annotated[str, Header(alias="X-authentik-groups")] = "",
) -> AdminIdentity:
    groups = _groups(x_authentik_groups)
    if not x_authentik_username or not groups.intersection(OWNER_GROUPS):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Gramly owner access is required")
    return AdminIdentity(username=x_authentik_username[:150], groups=groups)


AdminDep = Annotated[AdminIdentity, Depends(current_admin)]


async def mutation_admin(
    admin: AdminDep,
    x_gramly_admin_request: Annotated[str, Header(alias="X-Gramly-Admin-Request")] = "",
) -> AdminIdentity:
    if x_gramly_admin_request != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin request confirmation is required")
    return admin


MutationAdminDep = Annotated[AdminIdentity, Depends(mutation_admin)]


def _safe_url(value: str, *, telegraph_only: bool = False) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("HTTPS URL is required")
    if telegraph_only and parsed.hostname not in {"telegra.ph", "graph.org"}:
        raise ValueError("Telegraph URL is required")
    return value


class ManualInput(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9_-]{2,64}$")
    title: str = Field(min_length=1, max_length=160)
    telegraph_url: str = Field(min_length=1, max_length=2048)
    description: str = Field(default="", max_length=4000)
    is_onboarding: bool = False
    is_active: bool = True
    sort_order: int = Field(default=0, ge=-10_000, le=10_000)


class AnnouncementInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=4096)
    audience: str = Field(pattern=r"^(all|free|business)$")
    starts_at: datetime
    ends_at: datetime | None = None
    priority: int = Field(default=0, ge=-10_000, le=10_000)
    button_text: str = Field(default="", max_length=64)
    button_url: str = Field(default="", max_length=2048)


class TipInput(BaseModel):
    feature_key: str = Field(pattern=r"^[a-z0-9_-]{2,64}$")
    text: str = Field(min_length=1, max_length=2000)
    sort_order: int = Field(ge=-10_000, le=10_000)
    is_active: bool = True


class ContextHelpInput(BaseModel):
    feature_key: str = Field(pattern=r"^[a-z0-9_-]{2,64}$")
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(default="", max_length=4000)
    manual_id: int | None = None
    section_id: int | None = None
    is_active: bool = True


class PlanPricingInput(BaseModel):
    price_rub: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    price_xtr: int | None = Field(default=None, gt=0)
    referral_base_rub: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    crypto_pay_enabled: bool
    stars_enabled: bool


class CreativeInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4096)
    cta_text: str = Field(default="", max_length=64)
    cta_url: str = Field(default="", max_length=2048)
    weight: int = Field(default=1, ge=1, le=1000)
    is_active: bool = True


async def _audit(
    session: AsyncSession, admin: AdminIdentity, action: str, context: dict[str, object]
) -> None:
    session.add(
        EventLog(
            event_type="welcome_admin_mutation",
            level="security",
            message=action,
            context={"admin": admin.username, **context},
        )
    )


@router.get("/overview")
async def overview(_admin: AdminDep, session: SessionDep) -> dict[str, object]:
    counts: dict[str, object] = {}
    for name, model in (
        ("owners", Owner),
        ("manuals", Manual),
        ("announcements", Announcement),
        ("tips", Tip),
        ("notification_failures", OwnerNotification),
    ):
        statement = select(func.count(model.id))
        if model is OwnerNotification:
            statement = statement.where(OwnerNotification.status == "failed")
        counts[name] = int(await session.scalar(statement) or 0)
    return counts


@router.get("/content")
async def content(_admin: AdminDep, session: SessionDep) -> dict[str, object]:
    manuals = list((await session.scalars(select(Manual).order_by(Manual.sort_order, Manual.id))).all())
    announcements = list(
        (await session.scalars(select(Announcement).order_by(Announcement.created_at.desc()))).all()
    )
    tips = list((await session.scalars(select(Tip).order_by(Tip.feature_key, Tip.sort_order))).all())
    help_items = list(
        (await session.scalars(select(ContextualHelp).order_by(ContextualHelp.feature_key))).all()
    )
    return {
        "manuals": [
            {
                "id": row.id,
                "slug": row.slug,
                "title": row.title,
                "telegraph_url": row.telegraph_url,
                "description": row.description,
                "is_onboarding": row.is_onboarding,
                "is_active": row.is_active,
                "sort_order": row.sort_order,
            }
            for row in manuals
        ],
        "announcements": [
            {
                "id": row.id,
                "title": row.title,
                "body": row.body,
                "audience": row.audience,
                "starts_at": row.starts_at,
                "ends_at": row.ends_at,
                "is_active": row.is_active,
                "priority": row.priority,
                "button_text": row.button_text,
                "button_url": row.button_url,
            }
            for row in announcements
        ],
        "tips": [
            {
                "id": row.id,
                "feature_key": row.feature_key,
                "text": row.text,
                "sort_order": row.sort_order,
                "is_active": row.is_active,
            }
            for row in tips
        ],
        "contextual_help": [
            {
                "id": row.id,
                "feature_key": row.feature_key,
                "title": row.title,
                "body": row.body,
                "manual_id": row.manual_id,
                "section_id": row.section_id,
                "is_active": row.is_active,
            }
            for row in help_items
        ],
    }


@router.post("/manuals", status_code=status.HTTP_201_CREATED)
async def create_manual(
    payload: ManualInput, _admin: MutationAdminDep, session: SessionDep
) -> dict[str, object]:
    try:
        url = _safe_url(payload.telegraph_url, telegraph_only=True)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    row = Manual(**payload.model_dump(exclude={"telegraph_url"}), telegraph_url=url)
    session.add(row)
    await _audit(session, _admin, "manual_created", {"slug": row.slug})
    await session.commit()
    return {"id": row.id, "slug": row.slug}


@router.post("/announcements", status_code=status.HTTP_201_CREATED)
async def create_announcement(
    payload: AnnouncementInput, _admin: MutationAdminDep, session: SessionDep
) -> dict[str, object]:
    if payload.ends_at is not None and payload.ends_at <= payload.starts_at:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "End must be after start")
    if bool(payload.button_text) != bool(payload.button_url):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Button text and URL are paired")
    try:
        button_url = _safe_url(payload.button_url) if payload.button_url else ""
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    row = Announcement(
        **payload.model_dump(exclude={"button_url"}),
        button_url=button_url,
        is_active=True,
    )
    session.add(row)
    await session.flush()
    await _audit(session, _admin, "announcement_created", {"announcement_id": row.id})
    await session.commit()
    return {"id": row.id}


@router.post("/tips", status_code=status.HTTP_201_CREATED)
async def create_tip(payload: TipInput, _admin: MutationAdminDep, session: SessionDep) -> dict[str, object]:
    row = Tip(**payload.model_dump())
    session.add(row)
    await session.flush()
    await _audit(session, _admin, "tip_created", {"tip_id": row.id})
    await session.commit()
    return {"id": row.id}


@router.post("/contextual-help", status_code=status.HTTP_201_CREATED)
async def upsert_contextual_help(
    payload: ContextHelpInput, _admin: MutationAdminDep, session: SessionDep
) -> dict[str, object]:
    row = await session.scalar(
        select(ContextualHelp).where(ContextualHelp.feature_key == payload.feature_key)
    )
    if row is None:
        row = ContextualHelp(**payload.model_dump())
        session.add(row)
    else:
        for key, value in payload.model_dump().items():
            setattr(row, key, value)
    await session.flush()
    await _audit(session, _admin, "contextual_help_saved", {"feature": row.feature_key})
    await session.commit()
    return {"id": row.id, "feature_key": row.feature_key}


@router.get("/plans")
async def admin_plans(_admin: AdminDep, session: SessionDep) -> dict[str, object]:
    rows = list((await session.scalars(select(Plan).order_by(Plan.id))).all())
    return {
        "plans": [
            {
                "id": row.id,
                "slug": row.slug,
                "display_name": row.display_name,
                "price_rub": str(row.price_rub) if row.price_rub is not None else None,
                "price_xtr": row.price_xtr,
                "referral_base_rub": str(row.referral_base_rub) if row.referral_base_rub else None,
                "crypto_pay_enabled": row.crypto_pay_enabled,
                "stars_enabled": row.stars_enabled,
            }
            for row in rows
        ]
    }


@router.get("/payments/readiness")
async def payment_readiness(
    _admin: AdminDep, session: SessionDep, settings: SettingsDep
) -> dict[str, object]:
    """Expose configuration health without ever returning secret material."""
    keys = {
        "crypto_pay_bot_checkout",
        "crypto_pay_mini_app_checkout",
        "telegram_stars_checkout",
    }
    flags = list(
        (await session.scalars(select(FeatureFlag).where(FeatureFlag.key.in_(keys)))).all()
    )
    return {
        "crypto_pay": {
            "api_token_configured": bool(settings.crypto_pay_api_token),
            "webhook_secret_configured": bool(settings.crypto_pay_webhook_secret),
            "production_api": settings.crypto_pay_api_base_url.rstrip("/")
            == "https://pay.crypt.bot",
        },
        "telegram_stars": {
            "interface_bot_configured": bool(settings.interface_bot_token),
        },
        "feature_flags": {flag.key: flag.enabled for flag in flags},
    }


@router.post("/plans/{plan_id}/pricing")
async def update_plan_pricing(
    plan_id: int,
    payload: PlanPricingInput,
    _admin: MutationAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    plan = await session.get(Plan, plan_id, with_for_update=True)
    if plan is None or plan.slug != "business":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Business plan not found")
    if payload.crypto_pay_enabled and payload.price_rub is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "RUB price is required")
    if payload.stars_enabled and (payload.price_xtr is None or payload.referral_base_rub is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Stars price and referral RUB base are required",
        )
    for key, value in payload.model_dump().items():
        setattr(plan, key, value)
    await _audit(session, _admin, "business_pricing_updated", {"plan_id": plan.id})
    await session.commit()
    return {"id": plan.id, "updated": True}


@router.get("/advertising")
async def advertising(_admin: AdminDep, session: SessionDep) -> dict[str, object]:
    rows = list((await session.scalars(select(AdCreative).order_by(AdCreative.id))).all())
    result = []
    for row in rows:
        impressions, clicks = (
            await session.execute(
                select(
                    func.count(AdImpression.id), func.coalesce(func.sum(AdImpression.click_count), 0)
                ).where(AdImpression.creative_id == row.id, AdImpression.status == "sent")
            )
        ).one()
        result.append(
            {
                "id": row.id,
                "name": row.name,
                "text": row.text,
                "cta_text": row.cta_text,
                "cta_url": row.cta_url,
                "weight": row.weight,
                "is_active": row.is_active,
                "impressions": int(impressions),
                "clicks": int(clicks),
            }
        )
    return {"creatives": result}


@router.post("/advertising", status_code=status.HTTP_201_CREATED)
async def create_creative(
    payload: CreativeInput, _admin: MutationAdminDep, session: SessionDep
) -> dict[str, object]:
    if bool(payload.cta_text) != bool(payload.cta_url):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "CTA text and URL are paired")
    try:
        cta_url = _safe_url(payload.cta_url) if payload.cta_url else ""
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    row = AdCreative(**payload.model_dump(exclude={"cta_url"}), cta_url=cta_url)
    session.add(row)
    await session.flush()
    await _audit(session, _admin, "advertising_created", {"creative_id": row.id})
    await session.commit()
    return {"id": row.id}
