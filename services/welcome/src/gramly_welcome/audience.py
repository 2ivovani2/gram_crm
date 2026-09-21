from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .metrics import AUDIENCE_CONSENTS
from .models import (
    ClientMessage,
    Contact,
    ManagedBot,
    SubscriberChallenge,
)

CONSENT_VERSION = "2026-09"
CAPTCHA_EMOJI = ("🍋", "🦖", "👋", "⭐", "🚀", "💚")


def build_subscribe_token(step_id: int, channel_id: int, secret: str) -> str:
    raw = f"{step_id}_{channel_id}"
    signature = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:20]
    return f"sub_{raw}_{signature}"


def verify_subscribe_token(token: str, secret: str) -> tuple[int, int] | None:
    try:
        prefix, raw_step, raw_channel, supplied = token.split("_", 3)
        step_id = int(raw_step)
        channel_id = int(raw_channel)
    except (ValueError, TypeError):
        return None
    if prefix != "sub" or step_id <= 0 or channel_id < 0:
        return None
    expected = hmac.new(
        secret.encode(), f"{raw_step}_{raw_channel}".encode(), hashlib.sha256
    ).hexdigest()[:20]
    return (step_id, channel_id) if hmac.compare_digest(supplied, expected) else None


async def create_challenge(
    session: AsyncSession, *, bot_id: int, contact_id: int, step_id: int, channel_id: int
) -> SubscriberChallenge:
    now = datetime.now(UTC)
    existing = await session.scalar(
        select(SubscriberChallenge)
        .where(
            SubscriberChallenge.bot_id == bot_id,
            SubscriberChallenge.contact_id == contact_id,
            SubscriberChallenge.status == "pending",
            SubscriberChallenge.expires_at > now,
        )
        .order_by(SubscriberChallenge.id.desc())
        .limit(1)
    )
    if existing is not None:
        return existing
    options = list(secrets.SystemRandom().sample(CAPTCHA_EMOJI, 4))
    challenge = SubscriberChallenge(
        public_token=uuid.uuid4(),
        bot_id=bot_id,
        contact_id=contact_id,
        step_id=step_id,
        source_channel_id=channel_id,
        answer=secrets.choice(options),
        options=options,
        expires_at=now + timedelta(minutes=10),
    )
    session.add(challenge)
    await session.flush()
    session.add(
        ClientMessage(
            bot_id=bot_id,
            contact_id=contact_id,
            payload={
                "kind": "captcha",
                "challenge": str(challenge.public_token),
                "answer": challenge.answer,
                "options": options,
            },
        )
    )
    return challenge


async def answer_challenge(
    session: AsyncSession, *, bot_id: int, contact_id: int, public_token: str, answer: str
) -> str:
    try:
        token = uuid.UUID(public_token)
    except ValueError:
        return "invalid"
    challenge = await session.scalar(
        select(SubscriberChallenge)
        .where(
            SubscriberChallenge.public_token == token,
            SubscriberChallenge.bot_id == bot_id,
            SubscriberChallenge.contact_id == contact_id,
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    if challenge is None or challenge.status != "pending":
        AUDIENCE_CONSENTS.labels("invalid").inc()
        return "invalid"
    if challenge.expires_at <= now:
        challenge.status = "expired"
        AUDIENCE_CONSENTS.labels("expired").inc()
        return "expired"
    challenge.attempts += 1
    if not hmac.compare_digest(challenge.answer, answer):
        if challenge.attempts >= 3:
            challenge.status = "failed"
        AUDIENCE_CONSENTS.labels("wrong").inc()
        return "wrong"
    challenge.status = "passed"
    challenge.completed_at = now
    await session.execute(
        update(Contact)
        .where(Contact.id == contact_id, Contact.bot_id == bot_id)
        .values(
            consent_status="active",
            consented_at=now,
            consent_version=CONSENT_VERSION,
            consent_source=f"step:{challenge.step_id or 0}",
            source_channel_id=challenge.source_channel_id,
            unsubscribed_at=None,
            delivery_status="live",
            bot_started=True,
        )
    )
    session.add(
        ClientMessage(
            bot_id=bot_id,
            contact_id=contact_id,
            payload={"kind": "consent_confirmed"},
        )
    )
    AUDIENCE_CONSENTS.labels("passed").inc()
    return "passed"


async def unsubscribe(session: AsyncSession, *, bot_id: int, contact_id: int) -> None:
    now = datetime.now(UTC)
    await session.execute(
        update(Contact)
        .where(Contact.id == contact_id, Contact.bot_id == bot_id)
        .values(consent_status="unsubscribed", unsubscribed_at=now)
    )
    session.add(
        ClientMessage(bot_id=bot_id, contact_id=contact_id, payload={"kind": "unsubscribed"})
    )
    AUDIENCE_CONSENTS.labels("unsubscribed").inc()


def captcha_callback(token: str, answer: str) -> str:
    return f"consent:{token}:{answer}"


def client_message_text(payload: dict[str, Any]) -> str:
    return {
        "consent_confirmed": "✅ Готово. Вы будете получать полезные сообщения от этого бота. Отписаться можно командой /stop.",
        "unsubscribed": "Вы отписались от рассылок. Вернуться можно через кнопку подписки в приветствии.",
        "challenge_wrong": "Не совпало. Откройте кнопку подписки ещё раз и выберите указанный символ.",
        "challenge_expired": "Проверка устарела. Нажмите кнопку подписки в приветствии ещё раз.",
    }.get(str(payload.get("kind")), "")


@dataclass(frozen=True)
class ClientMessageContext:
    message: ClientMessage
    bot: ManagedBot
    contact: Contact


async def claim_client_messages(
    session: AsyncSession, *, worker_id: str, limit: int, lease_seconds: int
) -> list[ClientMessage]:
    now = datetime.now(UTC)
    rows = list(
        await session.scalars(
            select(ClientMessage)
            .where(
                or_(
                    and_(ClientMessage.status.in_(("scheduled", "retry")), ClientMessage.due_at <= now),
                    and_(ClientMessage.status == "processing", ClientMessage.lease_expires_at < now),
                )
            )
            .order_by(ClientMessage.due_at, ClientMessage.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    )
    for row in rows:
        row.status = "processing"
        row.attempts += 1
        row.lease_owner = worker_id
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
    await session.commit()
    return rows


async def load_client_message(session: AsyncSession, message_id: int) -> ClientMessageContext | None:
    row = await session.get(ClientMessage, message_id)
    if row is None:
        return None
    bot = await session.get(ManagedBot, row.bot_id)
    contact = await session.get(Contact, row.contact_id)
    if bot is None or contact is None:
        return None
    return ClientMessageContext(row, bot, contact)


async def finish_client_message(
    session: AsyncSession, message_id: int, worker_id: str, *, success: bool, error: str = ""
) -> None:
    await session.execute(
        update(ClientMessage)
        .where(ClientMessage.id == message_id, ClientMessage.lease_owner == worker_id)
        .values(
            status="sent" if success else "failed",
            sent_at=datetime.now(UTC) if success else None,
            error=error[:64],
            lease_owner=None,
            lease_expires_at=None,
        )
    )
    await session.commit()


async def defer_client_message(
    session: AsyncSession, message_id: int, worker_id: str, *, delay_seconds: int, error: str
) -> None:
    await session.execute(
        update(ClientMessage)
        .where(ClientMessage.id == message_id, ClientMessage.lease_owner == worker_id)
        .values(
            status="retry",
            due_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
            error=error[:64],
            lease_owner=None,
            lease_expires_at=None,
        )
    )
    await session.commit()
