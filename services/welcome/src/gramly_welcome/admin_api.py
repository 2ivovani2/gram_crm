from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, cast
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Select

from .billing import safe_invoice_snapshot, verify_and_settle_crypto_invoice
from .bot_transfer import BotTransferError, transfer_bot
from .business_metrics import live_business_metrics
from .config import Settings, get_settings
from .crypto_pay import CryptoPayClient, CryptoPayError
from .db import session_dependency
from .finance import FinanceError, schedule_subscription_reminders
from .models import (
    AdCreative,
    AdImpression,
    Announcement,
    CampaignAttribution,
    CampaignPayment,
    CampaignTouch,
    Channel,
    Contact,
    ContextualHelp,
    EventLog,
    FeatureFlag,
    FinancialLedgerEntry,
    FlowDelivery,
    LearningVideo,
    ManagedBot,
    Manual,
    MarketingCampaign,
    Owner,
    OwnerNotification,
    Payment,
    Plan,
    Subscription,
    SubscriptionReminder,
    Tip,
    WeeklyBusinessSnapshot,
)

router = APIRouter(prefix="/api/admin/v1", tags=["welcome-admin"])
SessionDep = Annotated[AsyncSession, Depends(session_dependency)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ADMIN_GROUPS = {"gramly-owners", "authentik Admins", "Business", "Product", "gramly-hello-admin"}


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
    if not x_authentik_username or not groups.intersection(ADMIN_GROUPS):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "GramlyHello Control access is required")
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


class LearningVideoInput(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9_-]{2,64}$")
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    video_url: str = Field(min_length=1, max_length=2048)
    poster_url: str = Field(default="", max_length=2048)
    duration_seconds: int = Field(default=0, ge=0, le=86_400)
    feature_key: str = Field(default="home", pattern=r"^[a-z0-9_-]{2,64}$")
    sort_order: int = Field(default=0, ge=-10_000, le=10_000)
    is_active: bool = True


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


class RequiredChannelInput(BaseModel):
    enabled: bool
    channel_id: int = Field(lt=0)
    title: str = Field(min_length=1, max_length=160)
    url: str = Field(min_length=1, max_length=2048)


@router.get("/required-channel")
async def get_required_channel(_admin: AdminDep, session: SessionDep) -> dict[str, object]:
    flag = await session.get(FeatureFlag, "required_news_channel")
    config = flag.config if flag and isinstance(flag.config, dict) else {}
    return {
        "enabled": bool(flag and flag.enabled),
        "channel_id": int(config.get("channel_id") or 0),
        "title": str(config.get("title") or "GRAMLY | Новости"),
        "url": str(config.get("url") or ""),
    }


@router.post("/required-channel")
async def update_required_channel(
    payload: RequiredChannelInput,
    admin: MutationAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    parsed = urlparse(payload.url.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"t.me", "telegram.me"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Telegram HTTPS URL is required")
    flag = await session.get(FeatureFlag, "required_news_channel", with_for_update=True)
    if flag is None:
        flag = FeatureFlag(key="required_news_channel")
        session.add(flag)
    flag.enabled = payload.enabled
    flag.config = {
        "channel_id": payload.channel_id,
        "title": payload.title.strip(),
        "url": payload.url.strip(),
    }
    await _audit(
        session,
        admin,
        "required_news_channel_updated",
        {"enabled": payload.enabled, "channel_id": payload.channel_id},
    )
    await session.commit()
    return {"ok": True}


class PlanPricingInput(BaseModel):
    price_rub: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    price_xtr: int | None = Field(default=None, gt=0)
    referral_base_rub: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    calculation_price_rub: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    crypto_pay_enabled: bool
    stars_enabled: bool


class CreativeInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4096)
    cta_text: str = Field(default="", max_length=64)
    cta_url: str = Field(default="", max_length=2048)
    weight: int = Field(default=1, ge=1, le=1000)
    is_active: bool = True


class SubscriptionActionInput(BaseModel):
    action: str = Field(pattern=r"^(grant_business|extend_business|cancel_renewal|revoke_now)$")
    days: int | None = Field(default=None, ge=1, le=3650)
    reason: str = Field(min_length=5, max_length=500)


class BotTransferInput(BaseModel):
    target_owner_id: int = Field(gt=0)
    reason: str = Field(min_length=5, max_length=500)


class CampaignInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    starts_at: datetime
    ends_at: datetime | None = None
    attribution_window_days: int = Field(default=30, ge=1, le=365)
    is_active: bool = True


class ActiveStateInput(BaseModel):
    is_active: bool


async def _audit(
    session: AsyncSession, admin: AdminIdentity, action: str, context: dict[str, object]
) -> None:
    owner_id = context.get("owner_id")
    session.add(
        EventLog(
            owner_id=owner_id if isinstance(owner_id, int) else None,
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


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


PAYMENT_STATUSES = {"created", "paid", "expired", "refunded", "failed"}
PAYMENT_PROVIDERS = {"crypto_pay", "telegram_stars", "manual"}


def _payment_filters(
    *,
    q: str,
    payment_status: str,
    provider: str,
    plan: str,
    created_from: datetime | None,
    created_to: datetime | None,
) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = []
    normalized = q.strip().lstrip("@").lower()
    if normalized:
        search = f"%{normalized}%"
        candidates: list[ColumnElement[bool]] = [
            func.lower(Owner.username).like(search),
            func.lower(Owner.first_name).like(search),
            func.lower(Owner.last_name).like(search),
            func.cast(Owner.telegram_id, String).like(search),
            func.cast(Payment.id, String).like(search),
            func.lower(func.coalesce(Payment.provider_invoice_id, "")).like(search),
        ]
        filters.append(or_(*candidates))
    if payment_status == "active":
        filters.append(Payment.status.not_in(("expired", "failed")))
    elif payment_status == "unpaid":
        filters.append(Payment.status.in_(("created", "expired", "failed")))
    elif payment_status != "all":
        if payment_status not in PAYMENT_STATUSES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown payment status")
        filters.append(Payment.status == payment_status)
    if provider != "all":
        if provider not in PAYMENT_PROVIDERS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown payment provider")
        filters.append(Payment.provider == provider)
    if plan != "all":
        filters.append(Plan.slug == plan.strip().lower())
    if created_from is not None:
        filters.append(Payment.created_at >= created_from)
    if created_to is not None:
        filters.append(Payment.created_at <= created_to)
    return filters


def _payment_row(payment: Payment, owner: Owner, plan: Plan) -> dict[str, object]:
    return {
        "id": payment.id,
        "provider_invoice_id": payment.provider_invoice_id,
        "provider": payment.provider,
        "status": payment.status,
        "amount_rub": str(payment.amount_rub),
        "original_amount": str(payment.original_amount) if payment.original_amount is not None else None,
        "original_currency": payment.original_currency,
        "paid_asset": payment.paid_asset,
        "exchange_rate_rub": (
            str(payment.exchange_rate_rub) if payment.exchange_rate_rub is not None else None
        ),
        "invoice_url": payment.invoice_url,
        "period_days": payment.period_days,
        "created_at": _iso(payment.created_at),
        "expires_at": _iso(payment.expires_at),
        "paid_at": _iso(payment.paid_at),
        "refunded_at": _iso(payment.refunded_at),
        "owner": {
            "id": owner.id,
            "telegram_id": owner.telegram_id,
            "username": owner.username,
            "name": " ".join(item for item in (owner.first_name, owner.last_name) if item),
        },
        "plan": {"id": plan.id, "slug": plan.slug, "name": plan.display_name},
    }


def _payment_base(filters: list[ColumnElement[bool]]) -> Select[tuple[Payment, Owner, Plan]]:
    return (
        select(Payment, Owner, Plan)
        .join(Owner, Owner.id == Payment.owner_id)
        .join(Plan, Plan.id == Payment.plan_id)
        .where(*filters)
    )


@router.get("/payments")
async def payments(
    _admin: AdminDep,
    session: SessionDep,
    q: str = "",
    payment_status: str = "active",
    provider: str = "all",
    plan: str = "all",
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    sort: str = "created_desc",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, object]:
    page = max(1, page)
    page_size = max(10, min(page_size, 100))
    filters = _payment_filters(
        q=q,
        payment_status=payment_status,
        provider=provider,
        plan=plan,
        created_from=created_from,
        created_to=created_to,
    )
    base = _payment_base(filters)
    aggregate = base.with_only_columns(Payment.status, Payment.amount_rub).subquery()
    summary_rows = (
        await session.execute(
            select(
                aggregate.c.status,
                func.count(),
                func.coalesce(func.sum(aggregate.c.amount_rub), 0),
            ).group_by(aggregate.c.status)
        )
    ).all()
    by_status: dict[str, dict[str, object]] = {
        str(row_status): {"count": int(count), "rub": str(amount)}
        for row_status, count, amount in summary_rows
    }
    total = sum(int(count) for _, count, _ in summary_rows)
    errors = sum(
        int(count) for row_status, count, _ in summary_rows if row_status in {"failed", "expired"}
    )
    sort_options: dict[str, ColumnElement[Any]] = {
        "created_desc": Payment.created_at.desc(),
        "created_asc": Payment.created_at.asc(),
        "paid_desc": Payment.paid_at.desc().nullslast(),
        "paid_asc": Payment.paid_at.asc().nullsfirst(),
        "amount_desc": Payment.amount_rub.desc(),
        "amount_asc": Payment.amount_rub.asc(),
    }
    if sort not in sort_options:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown payment sort")
    rows = (
        await session.execute(
            base.order_by(sort_options[sort], Payment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [_payment_row(payment, owner, payment_plan) for payment, owner, payment_plan in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "summary": {
            "count": total,
            "paid_rub": by_status.get("paid", {}).get("rub", "0"),
            "pending_rub": by_status.get("created", {}).get("rub", "0"),
            "refunded_rub": by_status.get("refunded", {}).get("rub", "0"),
            "errors": errors,
            "by_status": by_status,
        },
    }


async def _campaign_payload(
    session: AsyncSession, campaign: MarketingCampaign, settings: Settings
) -> dict[str, object]:
    clicks, visitors, new_free = (
        await session.execute(
            select(
                func.coalesce(func.sum(CampaignTouch.click_count), 0),
                func.count(CampaignTouch.id),
                func.count(CampaignTouch.id).filter(CampaignTouch.is_new_free.is_(True)),
            ).where(CampaignTouch.campaign_id == campaign.id)
        )
    ).one()
    paid_clients, revenue = (
        await session.execute(
            select(
                func.count(func.distinct(CampaignPayment.owner_id)),
                func.coalesce(func.sum(CampaignPayment.amount_rub), 0),
            ).where(CampaignPayment.campaign_id == campaign.id)
        )
    ).one()
    active_clients = int(
        await session.scalar(
            select(func.count(func.distinct(CampaignAttribution.owner_id)))
            .join(Subscription, Subscription.owner_id == CampaignAttribution.owner_id)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                CampaignAttribution.campaign_id == campaign.id,
                Plan.slug == "business",
                Subscription.status == "active",
                Subscription.ends_at > datetime.now(UTC),
            )
        )
        or 0
    )
    visitors_count = int(visitors or 0)
    paid_count = int(paid_clients or 0)
    return {
        "id": campaign.id,
        "name": campaign.name,
        "starts_at": _iso(campaign.starts_at),
        "ends_at": _iso(campaign.ends_at),
        "attribution_window_days": campaign.attribution_window_days,
        "is_active": campaign.is_active,
        "deep_link": f"https://t.me/{settings.interface_bot_username}?start=cmp_{campaign.public_token}",
        "metrics": {
            "clicks": int(clicks or 0),
            "unique_visitors": visitors_count,
            "new_free": int(new_free or 0),
            "paid_clients": paid_count,
            "active_clients": active_clients,
            "revenue_rub": str(revenue or 0),
            "click_to_paid_percent": (
                round(paid_count * 100 / visitors_count, 2) if visitors_count else 0
            ),
        },
    }


def _campaign_filters(
    q: str, state: str, starts_from: datetime | None, starts_to: datetime | None
) -> list[ColumnElement[bool]]:
    now = datetime.now(UTC)
    filters: list[ColumnElement[bool]] = []
    if q.strip():
        filters.append(func.lower(MarketingCampaign.name).like(f"%{q.strip().lower()}%"))
    if state == "active":
        filters.extend(
            [
                MarketingCampaign.is_active.is_(True),
                MarketingCampaign.starts_at <= now,
                or_(MarketingCampaign.ends_at.is_(None), MarketingCampaign.ends_at > now),
            ]
        )
    elif state == "scheduled":
        filters.append(MarketingCampaign.starts_at > now)
    elif state == "ended":
        filters.append(MarketingCampaign.ends_at <= now)
    elif state != "all":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown campaign state")
    if starts_from is not None:
        filters.append(MarketingCampaign.starts_at >= starts_from)
    if starts_to is not None:
        filters.append(MarketingCampaign.starts_at <= starts_to)
    return filters


@router.get("/campaigns")
async def campaigns(
    _admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    q: str = "",
    state: str = "all",
    starts_from: datetime | None = None,
    starts_to: datetime | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, object]:
    page = max(page, 1)
    page_size = max(10, min(page_size, 100))
    filters = _campaign_filters(q, state, starts_from, starts_to)
    total = int(
        await session.scalar(select(func.count(MarketingCampaign.id)).where(*filters)) or 0
    )
    rows = list(
        (
            await session.scalars(
                select(MarketingCampaign)
                .where(*filters)
                .order_by(MarketingCampaign.created_at.desc(), MarketingCampaign.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return {
        "items": [await _campaign_payload(session, row, settings) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/campaigns/export.csv")
async def export_campaigns(
    _admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    q: str = "",
    state: str = "all",
    starts_from: datetime | None = None,
    starts_to: datetime | None = None,
) -> Response:
    rows = list(
        (
            await session.scalars(
                select(MarketingCampaign)
                .where(*_campaign_filters(q, state, starts_from, starts_to))
                .order_by(MarketingCampaign.created_at.desc())
            )
        ).all()
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id", "name", "starts_at", "ends_at", "window_days", "clicks",
            "unique_visitors", "new_free", "paid_clients", "active_clients",
            "revenue_rub", "conversion_percent", "deep_link",
        ]
    )
    for row in rows:
        item = await _campaign_payload(session, row, settings)
        metrics = cast(dict[str, object], item["metrics"])
        writer.writerow(
            [
                item["id"], item["name"], item["starts_at"], item["ends_at"],
                item["attribution_window_days"], metrics["clicks"],
                metrics["unique_visitors"], metrics["new_free"], metrics["paid_clients"],
                metrics["active_clients"], metrics["revenue_rub"],
                metrics["click_to_paid_percent"], item["deep_link"],
            ]
        )
    return Response(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=gramly-campaigns.csv"},
    )


@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CampaignInput, admin: MutationAdminDep, session: SessionDep
) -> dict[str, object]:
    if payload.ends_at is not None and payload.ends_at <= payload.starts_at:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "End must be after start")
    row = MarketingCampaign(**payload.model_dump(), public_token=uuid.uuid4())
    session.add(row)
    await session.flush()
    await _audit(session, admin, "marketing_campaign_created", {"campaign_id": row.id})
    await session.commit()
    return {"id": row.id}


@router.post("/campaigns/{campaign_id}")
async def update_campaign(
    campaign_id: int,
    payload: CampaignInput,
    admin: MutationAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    if payload.ends_at is not None and payload.ends_at <= payload.starts_at:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "End must be after start")
    row = await session.get(MarketingCampaign, campaign_id, with_for_update=True)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campaign not found")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    await _audit(session, admin, "marketing_campaign_updated", {"campaign_id": row.id})
    await session.commit()
    return {"id": row.id, "updated": True}


@router.get("/payments/export.csv")
async def export_payments(
    _admin: AdminDep,
    session: SessionDep,
    q: str = "",
    payment_status: str = "active",
    provider: str = "all",
    plan: str = "all",
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> Response:
    filters = _payment_filters(
        q=q,
        payment_status=payment_status,
        provider=provider,
        plan=plan,
        created_from=created_from,
        created_to=created_to,
    )
    rows = (await session.execute(_payment_base(filters).order_by(Payment.id.desc()))).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "payment_id", "provider_invoice_id", "status", "provider", "amount_rub",
            "original_amount", "currency", "asset", "plan", "owner", "telegram_id",
            "created_at", "paid_at", "refunded_at",
        ]
    )
    for payment, owner, payment_plan in rows:
        writer.writerow(
            [
                payment.id, payment.provider_invoice_id or "", payment.status, payment.provider,
                payment.amount_rub, payment.original_amount or "", payment.original_currency,
                payment.paid_asset or "", payment_plan.slug, owner.username or "",
                owner.telegram_id, _iso(payment.created_at), _iso(payment.paid_at),
                _iso(payment.refunded_at),
            ]
        )
    filename = f"gramlyhello-invoices-{datetime.now(UTC).date().isoformat()}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    flags = list((await session.scalars(select(FeatureFlag).where(FeatureFlag.key.in_(keys)))).all())
    return {
        "crypto_pay": {
            "api_token_configured": bool(settings.crypto_pay_api_token),
            "webhook_secret_configured": bool(settings.crypto_pay_webhook_secret),
            "production_api": settings.crypto_pay_api_base_url.rstrip("/") == "https://pay.crypt.bot",
        },
        "telegram_stars": {
            "interface_bot_configured": bool(settings.interface_bot_token),
        },
        "feature_flags": {flag.key: flag.enabled for flag in flags},
    }


@router.get("/payments/{payment_id}")
async def payment_detail(
    payment_id: int, _admin: AdminDep, session: SessionDep
) -> dict[str, object]:
    row = (
        await session.execute(_payment_base([Payment.id == payment_id]))
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    payment, owner, payment_plan = row
    subscription = await session.scalar(
        select(Subscription).where(Subscription.owner_id == payment.owner_id)
    )
    ledger = list(
        (
            await session.scalars(
                select(FinancialLedgerEntry)
                .where(FinancialLedgerEntry.payment_id == payment.id)
                .order_by(FinancialLedgerEntry.id)
            )
        ).all()
    )
    audits = list(
        (
            await session.scalars(
                select(EventLog)
                .where(
                    EventLog.event_type == "welcome_admin_mutation",
                    EventLog.context["payment_id"].astext == str(payment.id),
                )
                .order_by(EventLog.id.desc())
                .limit(20)
            )
        ).all()
    )
    provider_snapshot = (
        payment.provider_payload if isinstance(payment.provider_payload, dict) else {}
    )
    result = _payment_row(payment, owner, payment_plan)
    result.update(
        {
            "subscription": {
                "status": subscription.status,
                "source": subscription.source,
                "starts_at": _iso(subscription.starts_at),
                "ends_at": _iso(subscription.ends_at),
                "auto_renew": subscription.auto_renew,
                "matches_invoice": subscription.external_reference == payment.provider_invoice_id,
            }
            if subscription is not None
            else None,
            "ledger": [
                {
                    "id": item.id,
                    "type": item.entry_type,
                    "amount_rub": str(item.amount_rub),
                    "rate_percent": str(item.rate_percent) if item.rate_percent is not None else None,
                    "reason": item.reason,
                    "created_at": _iso(item.created_at),
                }
                for item in ledger
            ],
            "verification": {
                "has_provider_reference": bool(payment.provider_invoice_id),
                "snapshot": {
                    key: provider_snapshot.get(key)
                    for key in (
                        "invoice_id", "status", "amount", "fiat", "paid_asset",
                        "paid_amount", "paid_fiat_rate", "snapshot_hash",
                        "telegram_payment_charge_id",
                    )
                    if key in provider_snapshot
                },
            },
            "audit": [
                {"message": item.message, "created_at": _iso(item.created_at), "context": item.context}
                for item in audits
            ],
        }
    )
    return result


@router.post("/payments/{payment_id}/reconcile")
async def reconcile_payment(
    payment_id: int,
    admin: MutationAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    result = "unchanged"
    detail = ""
    try:
        if payment.provider == "crypto_pay":
            if not payment.provider_invoice_id:
                raise FinanceError("Crypto Pay invoice reference is missing")
            client = CryptoPayClient(settings.crypto_pay_api_token, settings.crypto_pay_api_base_url)
            invoice = await client.get_invoice(payment.provider_invoice_id)
            if invoice.status == "paid":
                await verify_and_settle_crypto_invoice(session, client, payment.provider_invoice_id)
                result, detail = "settled", "Crypto Pay confirmed the paid invoice"
            elif invoice.status == "expired" and payment.status == "created":
                payment.status = "expired"
                payment.provider_payload = safe_invoice_snapshot(invoice)
                result, detail = "expired", "Crypto Pay confirmed invoice expiration"
            else:
                payment.provider_payload = safe_invoice_snapshot(invoice)
                detail = f"Crypto Pay status: {invoice.status}"
        elif payment.provider == "telegram_stars":
            snapshot_charge = str(payment.provider_payload.get("telegram_payment_charge_id") or "")
            trusted = bool(
                payment.status == "paid"
                and payment.provider_invoice_id
                and snapshot_charge == payment.provider_invoice_id
            )
            if not trusted:
                raise FinanceError("No trusted successful_payment event matches this Stars invoice")
            subscription = await session.scalar(
                select(Subscription).where(Subscription.owner_id == payment.owner_id)
            )
            result = "verified" if subscription is not None else "inconsistent"
            detail = (
                "Stars successful_payment, payment and subscription are linked"
                if subscription is not None and subscription.external_reference == payment.provider_invoice_id
                else "Stars payment is trusted, but the subscription link requires attention"
            )
        else:
            detail = "Manual payments have no external provider to reconcile"
    except (CryptoPayError, FinanceError) as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await _audit(
        session,
        admin,
        "payment_reconciled",
        {"payment_id": payment.id, "owner_id": payment.owner_id, "result": result, "detail": detail},
    )
    await session.commit()
    return {"ok": True, "result": result, "detail": detail}


def _subscription_is_business(subscription: Subscription | None, plan: Plan | None) -> bool:
    if subscription is None or plan is None or plan.slug != "business":
        return False
    if subscription.status != "active":
        return False
    return subscription.ends_at is None or subscription.ends_at > datetime.now(UTC)


@router.get("/dashboard")
async def dashboard(
    _admin: AdminDep,
    session: SessionDep,
    days: int = 30,
) -> dict[str, object]:
    days = max(7, min(days, 90))
    since = datetime.now(UTC) - timedelta(days=days)
    owners_total = int(await session.scalar(select(func.count(Owner.id))) or 0)
    owners_new = int(await session.scalar(select(func.count(Owner.id)).where(Owner.created_at >= since)) or 0)
    owners_active = int(
        await session.scalar(select(func.count(Owner.id)).where(Owner.last_seen_at >= since)) or 0
    )
    business_active = int(
        await session.scalar(
            select(func.count(Subscription.id))
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                Plan.slug == "business",
                Subscription.status == "active",
                or_(Subscription.ends_at.is_(None), Subscription.ends_at > datetime.now(UTC)),
            )
        )
        or 0
    )
    bot_count = int(
        await session.scalar(select(func.count(ManagedBot.id)).where(ManagedBot.is_active.is_(True))) or 0
    )
    channel_count = int(
        await session.scalar(select(func.count(Channel.id)).where(Channel.is_active.is_(True))) or 0
    )
    contact_count = int(await session.scalar(select(func.count(Contact.id))) or 0)
    delivery_rows = (
        await session.execute(
            select(FlowDelivery.status, func.count(FlowDelivery.id))
            .where(FlowDelivery.created_at >= since)
            .group_by(FlowDelivery.status)
        )
    ).all()
    deliveries = {str(status): int(count) for status, count in delivery_rows}
    paid = (
        await session.execute(
            select(func.count(Payment.id), func.coalesce(func.sum(Payment.amount_rub), 0)).where(
                Payment.status == "paid", Payment.paid_at >= since
            )
        )
    ).one()
    owner_series = (
        await session.execute(
            select(func.date(Owner.created_at), func.count(Owner.id))
            .where(Owner.created_at >= since)
            .group_by(func.date(Owner.created_at))
            .order_by(func.date(Owner.created_at))
        )
    ).all()
    delivery_series = (
        await session.execute(
            select(
                func.date(FlowDelivery.created_at),
                func.count(FlowDelivery.id),
                func.sum(case((FlowDelivery.status.in_(("failed", "partial", "unreachable")), 1), else_=0)),
            )
            .where(FlowDelivery.created_at >= since)
            .group_by(func.date(FlowDelivery.created_at))
            .order_by(func.date(FlowDelivery.created_at))
        )
    ).all()
    payment_series = (
        await session.execute(
            select(func.date(Payment.paid_at), func.coalesce(func.sum(Payment.amount_rub), 0))
            .where(Payment.status == "paid", Payment.paid_at >= since)
            .group_by(func.date(Payment.paid_at))
            .order_by(func.date(Payment.paid_at))
        )
    ).all()
    commercial_metrics = await live_business_metrics(session)
    weekly_snapshots = list(
        (
            await session.scalars(
                select(WeeklyBusinessSnapshot)
                .order_by(WeeklyBusinessSnapshot.week_start.desc())
                .limit(16)
            )
        ).all()
    )
    return {
        "range_days": days,
        "owners": {"total": owners_total, "new": owners_new, "active": owners_active},
        "business_active": business_active,
        "infrastructure": {"bots": bot_count, "channels": channel_count, "contacts": contact_count},
        "deliveries": deliveries,
        "payments": {"count": int(paid[0]), "rub": str(paid[1])},
        "commercial": {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in commercial_metrics.items()
        },
        "weekly_snapshots": [
            {
                "week_start": str(row.week_start),
                "calculation_price_rub": str(row.calculation_price_rub),
                "active_paid_clients": row.active_paid_clients,
                "first_paid_30d": row.first_paid_30d,
                "churned_7d": row.churned_7d,
                "weekly_revenue_run_rate_rub": str(row.weekly_revenue_run_rate_rub),
            }
            for row in reversed(weekly_snapshots)
        ],
        "series": {
            "owners": [{"date": str(day), "value": int(value)} for day, value in owner_series],
            "deliveries": [
                {"date": str(day), "total": int(total), "failed": int(failed or 0)}
                for day, total, failed in delivery_series
            ],
            "payments": [{"date": str(day), "rub": str(value)} for day, value in payment_series],
        },
    }


@router.get("/owners")
async def owners(
    _admin: AdminDep,
    session: SessionDep,
    q: str = "",
    plan: str = "all",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, object]:
    page = max(page, 1)
    page_size = max(10, min(page_size, 100))
    filters = []
    normalized = q.strip().lstrip("@").lower()
    if normalized:
        search = f"%{normalized}%"
        filters.append(
            or_(
                func.lower(Owner.username).like(search),
                func.lower(Owner.first_name).like(search),
                func.lower(Owner.last_name).like(search),
                func.cast(Owner.telegram_id, String).like(f"%{normalized}%"),
            )
        )
    now = datetime.now(UTC)
    business_condition = and_(
        Plan.slug == "business",
        Subscription.status == "active",
        or_(Subscription.ends_at.is_(None), Subscription.ends_at > now),
    )
    if plan == "business":
        filters.append(business_condition)
    elif plan == "free":
        filters.append(or_(Subscription.id.is_(None), ~business_condition))
    base = (
        select(Owner, Subscription, Plan)
        .outerjoin(Subscription, Subscription.owner_id == Owner.id)
        .outerjoin(Plan, Plan.id == Subscription.plan_id)
        .where(*filters)
    )
    total = int(await session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = (
        await session.execute(
            base.order_by(Owner.last_seen_at.desc(), Owner.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    result = []
    for owner, subscription, subscription_plan in rows:
        bot_ids = select(ManagedBot.id).where(ManagedBot.owner_id == owner.id)
        bots = int(
            await session.scalar(select(func.count(ManagedBot.id)).where(ManagedBot.owner_id == owner.id))
            or 0
        )
        channels = int(
            await session.scalar(select(func.count(Channel.id)).where(Channel.bot_id.in_(bot_ids))) or 0
        )
        contacts = int(
            await session.scalar(select(func.count(Contact.id)).where(Contact.bot_id.in_(bot_ids))) or 0
        )
        paid_rub = await session.scalar(
            select(func.coalesce(func.sum(Payment.amount_rub), 0)).where(
                Payment.owner_id == owner.id, Payment.status == "paid"
            )
        )
        result.append(
            {
                "id": owner.id,
                "telegram_id": owner.telegram_id,
                "username": owner.username,
                "first_name": owner.first_name,
                "last_name": owner.last_name,
                "created_at": _iso(owner.created_at),
                "last_seen_at": _iso(owner.last_seen_at),
                "plan": "business" if _subscription_is_business(subscription, subscription_plan) else "free",
                "plan_name": subscription_plan.display_name
                if _subscription_is_business(subscription, subscription_plan)
                else "Free",
                "subscription": {
                    "source": subscription.source,
                    "status": subscription.status,
                    "starts_at": _iso(subscription.starts_at),
                    "ends_at": _iso(subscription.ends_at),
                    "auto_renew": subscription.auto_renew,
                }
                if subscription
                else None,
                "usage": {"bots": bots, "channels": channels, "contacts": contacts},
                "paid_rub": str(paid_rub),
            }
        )
    return {"items": result, "total": total, "page": page, "page_size": page_size}


@router.get("/owners/{owner_id}")
async def owner_detail(owner_id: int, _admin: AdminDep, session: SessionDep) -> dict[str, object]:
    owner = await session.get(Owner, owner_id)
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Owner not found")
    subscription_row = (
        await session.execute(
            select(Subscription, Plan)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(Subscription.owner_id == owner_id)
        )
    ).one_or_none()
    bots = list(
        (
            await session.scalars(
                select(ManagedBot).where(ManagedBot.owner_id == owner_id).order_by(ManagedBot.id)
            )
        ).all()
    )
    payments = list(
        (
            await session.scalars(
                select(Payment).where(Payment.owner_id == owner_id).order_by(Payment.id.desc()).limit(20)
            )
        ).all()
    )
    events = list(
        (
            await session.scalars(
                select(EventLog)
                .where(EventLog.owner_id == owner_id, EventLog.event_type == "welcome_admin_mutation")
                .order_by(EventLog.created_at.desc())
                .limit(20)
            )
        ).all()
    )
    subscription, plan = subscription_row if subscription_row else (None, None)
    return {
        "owner": {
            "id": owner.id,
            "telegram_id": owner.telegram_id,
            "username": owner.username,
            "first_name": owner.first_name,
            "last_name": owner.last_name,
            "created_at": _iso(owner.created_at),
            "last_seen_at": _iso(owner.last_seen_at),
        },
        "subscription": {
            "plan": plan.slug,
            "plan_name": plan.display_name,
            "source": subscription.source,
            "status": subscription.status,
            "starts_at": _iso(subscription.starts_at),
            "ends_at": _iso(subscription.ends_at),
            "auto_renew": subscription.auto_renew,
        }
        if subscription is not None and plan is not None
        else None,
        "bots": [
            {
                "id": bot.id,
                "username": bot.username,
                "display_name": bot.display_name,
                "is_active": bot.is_active,
                "webhook_configured": bot.webhook_configured,
            }
            for bot in bots
        ],
        "payments": [
            {
                "id": payment.id,
                "provider": payment.provider,
                "status": payment.status,
                "amount_rub": str(payment.amount_rub),
                "created_at": _iso(payment.created_at),
                "paid_at": _iso(payment.paid_at),
            }
            for payment in payments
        ],
        "admin_events": [
            {"message": event.message, "context": event.context, "created_at": _iso(event.created_at)}
            for event in events
        ],
    }


@router.post("/bots/{bot_id}/transfer")
async def transfer_managed_bot(
    bot_id: int,
    payload: BotTransferInput,
    admin: MutationAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        result = await transfer_bot(
            session,
            bot_id=bot_id,
            target_owner_id=payload.target_owner_id,
            admin_username=admin.username,
            reason=payload.reason,
        )
        await session.commit()
    except BotTransferError as exc:
        await session.rollback()
        message = str(exc)
        code = status.HTTP_404_NOT_FOUND if message == "Bot not found" else status.HTTP_409_CONFLICT
        raise HTTPException(code, message) from exc
    return {
        "bot_id": result.bot_id,
        "source_owner_id": result.source_owner_id,
        "target_owner_id": result.target_owner_id,
        "moved": result.moved,
    }


async def _cancel_stars_renewal(settings: Settings, owner: Owner, subscription: Subscription) -> None:
    if subscription.source != "telegram_stars" or not subscription.auto_renew:
        return
    if not settings.interface_bot_token or not subscription.external_reference:
        raise HTTPException(status.HTTP_409_CONFLICT, "Stars renewal cannot be safely cancelled")
    url = f"https://api.telegram.org/bot{settings.interface_bot_token}/editUserStarSubscription"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url,
                json={
                    "user_id": owner.telegram_id,
                    "telegram_payment_charge_id": subscription.external_reference,
                    "is_canceled": True,
                },
            )
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Telegram subscription API is unavailable") from exc
    if not response.is_success or not payload.get("ok"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Telegram did not confirm renewal cancellation")


@router.post("/owners/{owner_id}/subscription")
async def manage_owner_subscription(
    owner_id: int,
    payload: SubscriptionActionInput,
    _admin: MutationAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    owner = await session.get(Owner, owner_id)
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Owner not found")
    existing = await session.scalar(
        select(Subscription).where(Subscription.owner_id == owner_id).with_for_update()
    )
    if payload.action in {"cancel_renewal", "revoke_now"}:
        if existing is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Owner has no subscription")
        await _cancel_stars_renewal(settings, owner, existing)
    now = datetime.now(UTC)
    business = await session.scalar(select(Plan).where(Plan.slug == "business", Plan.is_active.is_(True)))
    free = await session.scalar(select(Plan).where(Plan.slug == "free", Plan.is_active.is_(True)))
    if business is None or free is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Free and Business plans must be configured")
    if payload.action in {"grant_business", "extend_business"}:
        if payload.days is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Days are required")
        base = now
        if payload.action == "extend_business" and existing and existing.ends_at and existing.ends_at > now:
            base = existing.ends_at
        if existing is None:
            existing = Subscription(
                owner_id=owner_id, plan_id=business.id, source="manual", status="active", starts_at=now
            )
            session.add(existing)
        existing.plan_id = business.id
        existing.source = "manual"
        existing.status = "active"
        existing.starts_at = min(existing.starts_at, now) if existing.starts_at else now
        existing.ends_at = base + timedelta(days=payload.days)
        existing.auto_renew = False
        existing.external_reference = ""
    elif payload.action == "cancel_renewal":
        if existing is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Owner has no subscription")
        existing.auto_renew = False
    elif payload.action == "revoke_now":
        if existing is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Owner has no subscription")
        existing.plan_id = free.id
        existing.source = "free"
        existing.status = "active"
        existing.starts_at = now
        existing.ends_at = None
        existing.auto_renew = False
        existing.external_reference = ""
    if existing is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Subscription action could not be applied")
    await session.flush()
    if payload.action in {"grant_business", "extend_business"} and existing.ends_at is not None:
        await schedule_subscription_reminders(session, existing, existing.ends_at)
    elif payload.action == "revoke_now":
        await session.execute(
            delete(SubscriptionReminder).where(
                SubscriptionReminder.subscription_id == existing.id
            )
        )
    notification_labels = {
        "grant_business": "Business подключён вручную",
        "extend_business": "Business продлён",
        "cancel_renewal": "Автопродление отключено",
        "revoke_now": "Подписка Business аннулирована",
    }
    session.add(
        OwnerNotification(
            owner_id=owner_id,
            kind="subscription_change",
            dedupe_key=f"subscription:{payload.action}:{existing.id}:{uuid.uuid4().hex}",
            sequence=0,
            payload={
                "title": "💳 Изменение подписки",
                "body": f"{notification_labels[payload.action]}. Причина: {payload.reason}",
            },
            status="pending",
            due_at=now,
        )
    )
    await _audit(
        session,
        _admin,
        f"subscription_{payload.action}",
        {"owner_id": owner_id, "days": payload.days, "reason": payload.reason},
    )
    await session.commit()
    return {"owner_id": owner_id, "action": payload.action, "updated": True}


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
    videos = list(
        (await session.scalars(select(LearningVideo).order_by(LearningVideo.sort_order, LearningVideo.id))).all()
    )
    return {
        "videos": [
            {
                "id": row.id, "slug": row.slug, "title": row.title,
                "description": row.description, "video_url": row.video_url,
                "poster_url": row.poster_url, "duration_seconds": row.duration_seconds,
                "feature_key": row.feature_key, "sort_order": row.sort_order,
                "is_active": row.is_active,
            }
            for row in videos
        ],
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


@router.post("/learning-videos", status_code=status.HTTP_201_CREATED)
async def create_learning_video(
    payload: LearningVideoInput, _admin: MutationAdminDep, session: SessionDep
) -> dict[str, object]:
    try:
        video_url = _safe_url(payload.video_url)
        poster_url = _safe_url(payload.poster_url) if payload.poster_url else ""
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    row = LearningVideo(
        **payload.model_dump(exclude={"video_url", "poster_url"}),
        video_url=video_url,
        poster_url=poster_url,
    )
    session.add(row)
    await session.flush()
    await _audit(session, _admin, "learning_video_created", {"video_id": row.id, "slug": row.slug})
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
                "calculation_price_rub": (
                    str(row.calculation_price_rub) if row.calculation_price_rub else None
                ),
                "crypto_pay_enabled": row.crypto_pay_enabled,
                "stars_enabled": row.stars_enabled,
            }
            for row in rows
        ]
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


@router.post("/resources/{resource}/{resource_id}/active")
async def set_resource_active(
    resource: str,
    resource_id: int,
    payload: ActiveStateInput,
    _admin: MutationAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    models = {
        "manual": Manual,
        "announcement": Announcement,
        "tip": Tip,
        "advertising": AdCreative,
        "learning-video": LearningVideo,
    }
    model = models.get(resource)
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resource type not found")
    row = cast(Any, await session.get(model, resource_id, with_for_update=True))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resource not found")
    row.is_active = payload.is_active
    await _audit(
        session,
        _admin,
        "resource_active_changed",
        {"resource": resource, "resource_id": resource_id, "is_active": payload.is_active},
    )
    await session.commit()
    return {"id": resource_id, "is_active": payload.is_active}
