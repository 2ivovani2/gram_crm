"""Product-facing admin aggregates. No contact identities leave this module."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Select

from .models import (
    Channel,
    Contact,
    ContentFlow,
    ContentFlowVersion,
    DeliveryOperation,
    FlowDelivery,
    ManagedBot,
    Owner,
    Payment,
    SubscriberChallenge,
)


def reachable_contacts() -> tuple[ColumnElement[bool], ...]:
    """The same opt-in requirements as client broadcasts, plus known reachability."""
    return (
        Contact.consent_status == "active",
        Contact.bot_started.is_(True),
        Contact.delivery_status != "dead",
        ManagedBot.is_active.is_(True),
    )


def high_activity_owner_ids(as_of: datetime) -> Select[tuple[int]]:
    weekly_owner_contacts = (
        select(ManagedBot.owner_id.label("owner_id"), Contact.telegram_id.label("telegram_id"))
        .join(ManagedBot, ManagedBot.id == Contact.bot_id)
        .where(
            *reachable_contacts(), Contact.consented_at >= as_of - timedelta(days=7),
            Contact.consented_at < as_of,
        )
        .distinct()
        .subquery()
    )
    return (
        select(weekly_owner_contacts.c.owner_id)
        .group_by(weekly_owner_contacts.c.owner_id)
        .having(func.count() >= 100)
    )


async def product_dashboard_metrics(
    session: AsyncSession, *, as_of: datetime, since: datetime
) -> dict[str, object]:
    week_since = as_of - timedelta(days=7)
    bot_rows = (
        await session.execute(
            select(ManagedBot.is_active, func.count(ManagedBot.id))
            .group_by(ManagedBot.is_active)
        )
    ).all()
    bot_counts = {bool(active): int(count) for active, count in bot_rows}
    owners_with_bots = int(
        await session.scalar(
            select(func.count(func.distinct(ManagedBot.owner_id))).where(ManagedBot.is_active.is_(True))
        ) or 0
    )

    high_activity = int(
        await session.scalar(
            select(func.count()).select_from(high_activity_owner_ids(as_of).subquery())
        ) or 0
    )
    audience = (
        await session.execute(
            select(
                func.count(Contact.id),
                func.count(func.distinct(Contact.telegram_id)),
            )
            .join(ManagedBot, ManagedBot.id == Contact.bot_id)
            .where(*reachable_contacts())
        )
    ).one()
    all_contacts = int(await session.scalar(select(func.count(Contact.id))) or 0)

    # A refunded invoice was paid before the refund. Use paid_at for gross and
    # refunded_at for outgoing money, so a later refund can make net negative.
    paid_statuses = ("paid", "refunded")
    paid = (
        await session.execute(
            select(
                func.count(Payment.id),
                func.coalesce(func.sum(Payment.amount_rub), 0),
                func.count(func.distinct(Payment.owner_id)),
            ).where(
                Payment.status.in_(paid_statuses),
                Payment.paid_at >= since,
                Payment.paid_at < as_of,
            )
        )
    ).one()
    refunded = (
        await session.execute(
            select(func.count(Payment.id), func.coalesce(func.sum(Payment.amount_rub), 0))
            .where(Payment.status == "refunded", Payment.refunded_at >= since, Payment.refunded_at < as_of)
        )
    ).one()
    providers = (
        await session.execute(
            select(Payment.provider, func.count(Payment.id), func.coalesce(func.sum(Payment.amount_rub), 0))
            .where(Payment.status.in_(paid_statuses), Payment.paid_at >= since, Payment.paid_at < as_of)
            .group_by(Payment.provider)
        )
    ).all()
    gross = Decimal(paid[1])
    refund_amount = Decimal(refunded[1])

    # These are current owner milestones, not a reconstructed historical cohort.
    owner_total = int(await session.scalar(select(func.count(Owner.id))) or 0)
    with_channel = int(
        await session.scalar(
            select(func.count(func.distinct(ManagedBot.owner_id)))
            .join(Channel, Channel.bot_id == ManagedBot.id)
            .where(ManagedBot.is_active.is_(True), Channel.is_active.is_(True))
        ) or 0
    )
    with_published = int(
        await session.scalar(
            select(func.count(func.distinct(ManagedBot.owner_id)))
            .join(Channel, Channel.bot_id == ManagedBot.id)
            .join(ContentFlow, ContentFlow.bot_id == ManagedBot.id)
            .join(ContentFlowVersion, ContentFlowVersion.flow_id == ContentFlow.id)
            .where(
                ManagedBot.is_active.is_(True), Channel.is_active.is_(True), ContentFlow.is_active.is_(True),
                ContentFlow.kind == "welcome", ContentFlowVersion.status == "published",
            )
        ) or 0
    )
    published_version = aliased(ContentFlowVersion)
    with_delivery = int(
        await session.scalar(
            select(func.count(func.distinct(ManagedBot.owner_id)))
            .join(FlowDelivery, FlowDelivery.bot_id == ManagedBot.id)
            .join(Channel, Channel.id == FlowDelivery.channel_id)
            .join(ContentFlowVersion, ContentFlowVersion.id == FlowDelivery.version_id)
            .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
            .join(published_version, published_version.flow_id == ContentFlow.id)
            .where(
                ManagedBot.is_active.is_(True), Channel.is_active.is_(True),
                FlowDelivery.status == "completed", ContentFlow.is_active.is_(True),
                published_version.status == "published",
                ContentFlow.kind == "welcome",
            )
        ) or 0
    )
    delivery_rows = (
        await session.execute(
            select(FlowDelivery.status, func.count(FlowDelivery.id))
            .join(ContentFlowVersion, ContentFlowVersion.id == FlowDelivery.version_id)
            .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
            .where(
                FlowDelivery.created_at >= since, FlowDelivery.created_at < as_of,
                ContentFlow.kind == "welcome",
            )
            .group_by(FlowDelivery.status)
        )
    ).all()
    delivery_counts = {str(status): int(count) for status, count in delivery_rows}
    expired = int(
        await session.scalar(
            select(func.count(func.distinct(DeliveryOperation.flow_delivery_id)))
            .join(FlowDelivery, FlowDelivery.id == DeliveryOperation.flow_delivery_id)
            .join(ContentFlowVersion, ContentFlowVersion.id == FlowDelivery.version_id)
            .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
            .where(
                FlowDelivery.created_at >= since, FlowDelivery.created_at < as_of,
                DeliveryOperation.error == "join_request_window_expired",
                ContentFlow.kind == "welcome",
            )
        ) or 0
    )
    payment_day = func.date(func.timezone("UTC", Payment.paid_at))
    consent_day = func.date(func.timezone("UTC", SubscriberChallenge.completed_at))
    payments_daily = (
        await session.execute(
            select(payment_day, func.count(Payment.id), func.coalesce(func.sum(Payment.amount_rub), 0))
            .where(Payment.status.in_(paid_statuses), Payment.paid_at >= since, Payment.paid_at < as_of)
            .group_by(payment_day)
            .order_by(payment_day)
        )
    ).all()
    consents_daily = (
        await session.execute(
            select(consent_day, func.count(func.distinct(Contact.telegram_id)))
            .select_from(SubscriberChallenge)
            .join(Contact, Contact.id == SubscriberChallenge.contact_id)
            .where(
                SubscriberChallenge.status == "passed",
                SubscriberChallenge.completed_at >= since,
                SubscriberChallenge.completed_at < as_of,
            )
            .group_by(consent_day)
            .order_by(consent_day)
        )
    ).all()

    return {
        "bots": {"active": bot_counts.get(True, 0), "inactive": bot_counts.get(False, 0)},
        "owners_with_bots": owners_with_bots,
        "high_activity_owners": {"count": high_activity, "threshold": 100, "window_start": week_since.isoformat()},
        "audience": {"unique_people": int(audience[1]), "bot_subscriptions": int(audience[0]), "all_contacts": all_contacts},
        "payment_metrics": {
            "paid_count": int(paid[0]), "gross_rub": str(gross), "paying_owners": int(paid[2]),
            "refund_count": int(refunded[0]), "refund_rub": str(refund_amount), "net_rub": str(gross - refund_amount),
            "by_provider": {str(provider): {"count": int(count), "gross_rub": str(amount)} for provider, count, amount in providers},
        },
        "activation_funnel": {
            "owners": owner_total, "with_bot": owners_with_bots, "with_channel": with_channel,
            "with_published_welcome": with_published, "with_completed_delivery": with_delivery,
        },
        "delivery_health": {
            "completed": delivery_counts.get("completed", 0), "partial": delivery_counts.get("partial", 0),
            "failed": delivery_counts.get("failed", 0) + delivery_counts.get("unreachable", 0),
            "expired_join_windows": expired,
        },
        "growth_series": {
            "payments": [{"date": str(day), "count": int(count), "gross_rub": str(amount)} for day, count, amount in payments_daily],
            "new_consents": [{"date": str(day), "count": int(count)} for day, count in consents_daily],
        },
    }
