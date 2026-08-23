from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatMember, ChatMemberUpdated
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import FeatureFlag, Owner, RequiredChannelMembership

FEATURE_KEY = "required_news_channel"
POSITIVE_CACHE_SECONDS = 60
TELEGRAM_ERROR_GRACE_SECONDS = 15 * 60
ALLOWED_STATUSES = {"member", "administrator", "creator"}


@dataclass(frozen=True)
class RequiredChannelConfig:
    enabled: bool
    channel_id: int
    title: str
    url: str


@dataclass(frozen=True)
class MembershipResult:
    allowed: bool
    config: RequiredChannelConfig
    status: str
    checked_at: datetime | None
    temporarily_unavailable: bool = False


def _status_value(member: ChatMember) -> str:
    raw = getattr(member, "status", "unknown")
    return str(getattr(raw, "value", raw))


def _member_allowed(status: str, is_member: bool) -> bool:
    return status in ALLOWED_STATUSES or (status == "restricted" and is_member)


async def required_channel_config(session: AsyncSession) -> RequiredChannelConfig:
    flag = await session.get(FeatureFlag, FEATURE_KEY)
    config: dict[str, Any] = flag.config if flag and isinstance(flag.config, dict) else {}
    return RequiredChannelConfig(
        enabled=bool(flag and flag.enabled),
        channel_id=int(config.get("channel_id") or 0),
        title=str(config.get("title") or "GRAMLY | Новости"),
        url=str(config.get("url") or ""),
    )


async def _persist(
    session: AsyncSession,
    *,
    owner_id: int,
    channel_id: int,
    status: str,
    is_member: bool,
    checked_at: datetime,
) -> RequiredChannelMembership:
    row_id = await session.scalar(
        insert(RequiredChannelMembership)
        .values(
            owner_id=owner_id,
            channel_id=channel_id,
            status=status,
            is_member=is_member,
            checked_at=checked_at,
            updated_at=checked_at,
        )
        .on_conflict_do_update(
            constraint="uq_required_channel_owner_channel",
            set_={
                "status": status,
                "is_member": is_member,
                "checked_at": checked_at,
                "updated_at": checked_at,
            },
        )
        .returning(RequiredChannelMembership.id)
    )
    await session.commit()
    row = await session.get(RequiredChannelMembership, row_id)
    if row is None:
        raise RuntimeError("Required channel membership upsert failed")
    return row


async def check_required_membership(
    session: AsyncSession,
    bot: Bot,
    owner: Owner,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> MembershipResult:
    current = now or datetime.now(UTC)
    config = await required_channel_config(session)
    if not config.enabled or not config.channel_id:
        return MembershipResult(True, config, "disabled", None)
    row = await session.scalar(
        select(RequiredChannelMembership).where(
            RequiredChannelMembership.owner_id == owner.id,
            RequiredChannelMembership.channel_id == config.channel_id,
        )
    )
    if (
        not force
        and row is not None
        and _member_allowed(row.status, row.is_member)
        and row.checked_at is not None
        and row.checked_at > current - timedelta(seconds=POSITIVE_CACHE_SECONDS)
    ):
        return MembershipResult(True, config, row.status, row.checked_at)
    try:
        member = await bot.get_chat_member(config.channel_id, owner.telegram_id)
    except TelegramAPIError:
        if (
            row is not None
            and _member_allowed(row.status, row.is_member)
            and row.checked_at is not None
            and row.checked_at > current - timedelta(seconds=TELEGRAM_ERROR_GRACE_SECONDS)
        ):
            return MembershipResult(
                True, config, row.status, row.checked_at, temporarily_unavailable=True
            )
        return MembershipResult(
            False,
            config,
            row.status if row is not None else "unknown",
            row.checked_at if row is not None else None,
            temporarily_unavailable=True,
        )
    status = _status_value(member)
    is_member = bool(getattr(member, "is_member", status in ALLOWED_STATUSES))
    row = await _persist(
        session,
        owner_id=owner.id,
        channel_id=config.channel_id,
        status=status,
        is_member=is_member,
        checked_at=current,
    )
    return MembershipResult(_member_allowed(status, is_member), config, status, row.checked_at)


async def record_required_membership_update(
    session: AsyncSession, update: ChatMemberUpdated, *, now: datetime | None = None
) -> bool:
    config = await required_channel_config(session)
    if not config.enabled or update.chat.id != config.channel_id:
        return False
    owner = await session.scalar(select(Owner).where(Owner.telegram_id == update.new_chat_member.user.id))
    if owner is None:
        return False
    status = _status_value(update.new_chat_member)
    is_member = bool(getattr(update.new_chat_member, "is_member", status in ALLOWED_STATUSES))
    await _persist(
        session,
        owner_id=owner.id,
        channel_id=config.channel_id,
        status=status,
        is_member=is_member,
        checked_at=now or datetime.now(UTC),
    )
    return True
