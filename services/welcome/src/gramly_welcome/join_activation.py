from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .flow_delivery import schedule_content_flow
from .models import (
    Channel,
    Contact,
    FeatureFlag,
    JoinRequest,
    JoinRequestActivation,
    ManagedBot,
)

ACTIVATION_TTL = timedelta(hours=24)


def build_activation_token(public_id: uuid.UUID, bot_secret: str) -> str:
    signature = hmac.new(bot_secret.encode(), public_id.bytes, hashlib.sha256).digest()[:16]
    encoded = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{public_id.hex}_{encoded}"


def activation_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def activation_token_valid(activation: JoinRequestActivation, bot: ManagedBot, token: str) -> bool:
    expected = build_activation_token(activation.public_id, bot.path_secret)
    return hmac.compare_digest(token, expected) and hmac.compare_digest(
        activation.token_digest, activation_token_digest(token)
    )


async def long_activation_enabled(session: AsyncSession, bot_id: int) -> bool:
    flag = await session.get(FeatureFlag, "join_request_long_flow_activation")
    if flag is None or not flag.enabled:
        return False
    configured = flag.config.get("bot_ids", []) if isinstance(flag.config, dict) else []
    if not isinstance(configured, list) or not configured:
        return True
    return bot_id in {int(value) for value in configured if str(value).isdigit()}


async def create_or_refresh_activation(
    session: AsyncSession,
    *,
    request_id: int,
    bot: ManagedBot,
    contact_id: int,
    now: datetime,
) -> JoinRequestActivation:
    existing = await session.scalar(
        select(JoinRequestActivation)
        .where(JoinRequestActivation.join_request_id == request_id)
        .with_for_update()
    )
    public_id = uuid.uuid4()
    token = build_activation_token(public_id, bot.path_secret)
    values = {
        "public_id": public_id,
        "token_digest": activation_token_digest(token),
        "status": "pending",
        "due_at": now,
        "expires_at": now + ACTIVATION_TTL,
        "attempts": 0,
        "lease_owner": None,
        "lease_expires_at": None,
        "sent_at": None,
        "granted_at": None,
        "delivery_id": None,
        "error": "",
    }
    if existing is None:
        existing = JoinRequestActivation(
            join_request_id=request_id,
            bot_id=bot.id,
            contact_id=contact_id,
            **values,
        )
        session.add(existing)
        await session.flush()
        return existing
    for key, value in values.items():
        setattr(existing, key, value)
    existing.bot_id = bot.id
    existing.contact_id = contact_id
    await session.flush()
    return existing


@dataclass(frozen=True)
class ActivationContext:
    activation: JoinRequestActivation
    request: JoinRequest
    bot: ManagedBot
    channel: Channel
    contact: Contact


async def load_activation_context(
    session: AsyncSession,
    *,
    public_id: uuid.UUID | None = None,
    activation_id: int | None = None,
    for_update: bool = False,
) -> ActivationContext | None:
    query = select(JoinRequestActivation)
    if public_id is not None:
        query = query.where(JoinRequestActivation.public_id == public_id)
    elif activation_id is not None:
        query = query.where(JoinRequestActivation.id == activation_id)
    else:
        raise ValueError("activation identifier is required")
    if for_update:
        query = query.with_for_update()
    activation = await session.scalar(query)
    if activation is None:
        return None
    request_query = select(JoinRequest).where(JoinRequest.id == activation.join_request_id)
    contact_query = select(Contact).where(Contact.id == activation.contact_id)
    if for_update:
        request_query = request_query.with_for_update()
        contact_query = contact_query.with_for_update()
    request = await session.scalar(request_query)
    bot = await session.get(ManagedBot, activation.bot_id)
    channel = await session.get(Channel, request.channel_id) if request is not None else None
    contact = await session.scalar(contact_query)
    if request is None or bot is None or channel is None or contact is None:
        return None
    return ActivationContext(activation, request, bot, channel, contact)


async def claim_activation_batch(
    session: AsyncSession, *, worker_id: str, limit: int, lease_seconds: int
) -> list[JoinRequestActivation]:
    now = datetime.now(UTC)
    async with session.begin():
        rows = list(
            (
                await session.scalars(
                    select(JoinRequestActivation)
                    .where(
                        or_(
                            and_(
                                JoinRequestActivation.status == "pending",
                                JoinRequestActivation.due_at <= now,
                            ),
                            and_(
                                JoinRequestActivation.status == "processing",
                                JoinRequestActivation.lease_expires_at < now,
                            ),
                        )
                    )
                    .order_by(JoinRequestActivation.due_at, JoinRequestActivation.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        lease_until = now + timedelta(seconds=lease_seconds)
        for row in rows:
            row.status = "processing"
            row.lease_owner = worker_id
            row.lease_expires_at = lease_until
            row.attempts += 1
    return rows


async def defer_activation(
    session: AsyncSession,
    activation_id: int,
    worker_id: str,
    *,
    delay_seconds: int,
    error: str,
) -> None:
    await session.execute(
        update(JoinRequestActivation)
        .where(
            JoinRequestActivation.id == activation_id,
            JoinRequestActivation.lease_owner == worker_id,
        )
        .values(
            status="pending",
            due_at=datetime.now(UTC) + timedelta(seconds=max(1, delay_seconds)),
            lease_owner=None,
            lease_expires_at=None,
            error=error[:500],
        )
    )
    await session.commit()


async def finish_activation_message(
    session: AsyncSession,
    activation_id: int,
    worker_id: str,
    *,
    success: bool,
    error: str = "",
) -> None:
    now = datetime.now(UTC)
    await session.execute(
        update(JoinRequestActivation)
        .where(
            JoinRequestActivation.id == activation_id,
            JoinRequestActivation.lease_owner == worker_id,
        )
        .values(
            status="sent" if success else "failed",
            sent_at=now if success else None,
            lease_owner=None,
            lease_expires_at=None,
            error=error[:500],
        )
    )
    await session.commit()


async def grant_activation(
    session: AsyncSession,
    *,
    context: ActivationContext,
    event_key: str,
    now: datetime | None = None,
) -> int | None:
    current = now or datetime.now(UTC)
    activation = context.activation
    if activation.granted_at is not None:
        return activation.delivery_id
    if activation.expires_at <= current:
        activation.status = "expired"
        activation.error = "activation_expired"
        return None
    context.contact.bot_started = True
    delivery_id = await schedule_content_flow(
        session,
        bot=context.bot,
        channel_id=context.channel.id,
        contact_id=context.contact.id,
        event_key=event_key,
    )
    activation.status = "granted"
    activation.granted_at = current
    activation.delivery_id = delivery_id
    activation.error = ""
    if context.bot.auto_approve and context.request.status in {"pending", "scheduled", "processing"}:
        context.request.status = "scheduled"
        context.request.due_at = current
        context.request.lease_owner = None
        context.request.lease_expires_at = None
        context.request.error = ""
    return delivery_id
