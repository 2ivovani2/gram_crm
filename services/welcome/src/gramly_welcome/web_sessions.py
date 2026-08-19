from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Owner, WebSession
from .telegram_auth import VerifiedInitData


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class CreatedWebSession:
    token: str
    csrf_token: str
    expires_at: datetime
    owner: Owner


@dataclass(frozen=True)
class AuthenticatedWebSession:
    session_id: uuid.UUID
    owner: Owner
    expires_at: datetime


async def create_web_session(
    session: AsyncSession,
    init_data: VerifiedInitData,
    *,
    lifetime_seconds: int,
    now: datetime | None = None,
) -> CreatedWebSession:
    current = now or datetime.now(UTC)
    user = init_data.user
    owner_id = await session.scalar(
        insert(Owner)
        .values(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            last_seen_at=current,
        )
        .on_conflict_do_update(
            constraint="owner_telegram_id_key",
            set_={
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "last_seen_at": current,
            },
        )
        .returning(Owner.id)
    )
    owner = await session.get(Owner, owner_id)
    if owner is None:
        raise RuntimeError("Owner upsert did not return a record")

    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = current + timedelta(seconds=lifetime_seconds)
    session.add(
        WebSession(
            id=uuid.uuid4(),
            owner_id=owner.id,
            token_hash=_digest(token),
            csrf_hash=_digest(csrf_token),
            telegram_auth_date=init_data.auth_date,
            expires_at=expires_at,
        )
    )
    await session.commit()
    return CreatedWebSession(token, csrf_token, expires_at, owner)


async def authenticate_web_session(
    session: AsyncSession,
    token: str,
    *,
    now: datetime | None = None,
) -> AuthenticatedWebSession | None:
    if not token:
        return None
    current = now or datetime.now(UTC)
    row = (
        await session.execute(
            select(WebSession, Owner)
            .join(Owner, Owner.id == WebSession.owner_id)
            .where(
                WebSession.token_hash == _digest(token),
                WebSession.revoked_at.is_(None),
                WebSession.expires_at > current,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    web_session, owner = row
    return AuthenticatedWebSession(web_session.id, owner, web_session.expires_at)


async def csrf_token_valid(
    session: AsyncSession, session_id: uuid.UUID, csrf_token: str
) -> bool:
    if not csrf_token:
        return False
    expected = await session.scalar(
        select(WebSession.csrf_hash).where(WebSession.id == session_id)
    )
    return bool(expected and secrets.compare_digest(expected, _digest(csrf_token)))


async def revoke_web_session(session: AsyncSession, session_id: uuid.UUID) -> None:
    await session.execute(
        update(WebSession)
        .where(WebSession.id == session_id, WebSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()
