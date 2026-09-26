"""Private broadcast channels entered through an explicit invite link."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select
from telethon import functions, types, utils  # type: ignore[import-not-found,import-untyped]
from telethon.errors import (  # type: ignore[import-not-found,import-untyped]
    ChannelPrivateError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    InviteRequestSentError,
)

from .config import get_settings
from .crypto import TokenKeyring
from .db import session_factory
from .models import ViewChannelSnapshot, ViewMtprotoAccount, ViewPrivateChannel
from .view_collector import collect_one
from .view_pool import ViewPoolError, lease_account

INVITE_PATTERN = re.compile(
    r"(?<![\w/])(?:https?://)?(?:t\.me|telegram\.me)/(?:joinchat/|\+)([A-Za-z0-9_-]{8,128})(?![\w-])",
    re.I,
)


def parse_invites(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(1) for match in INVITE_PATTERN.finditer(text)))[:10]


class PrivateChannelError(ValueError):
    pass


async def connect_private(owner_id: int, invite_hash: str) -> tuple[int, str, int | None, datetime]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", invite_hash):
        raise PrivateChannelError("Ссылка-приглашение не распознана")
    fingerprint = sha256(invite_hash.encode("ascii")).hexdigest()
    async with session_factory() as session:
        assigned = await session.scalar(select(ViewPrivateChannel.account_id).join(
            ViewMtprotoAccount, ViewMtprotoAccount.id == ViewPrivateChannel.account_id,
        ).where(
            ViewPrivateChannel.invite_fingerprint == fingerprint,
            ViewPrivateChannel.status == "active",
            ViewMtprotoAccount.status == "healthy",
            ViewMtprotoAccount.disabled.is_(False),
        ).limit(1))
    try:
        async with lease_account(assigned, for_private_join=assigned is None) as (account_id, client):
            invite = await client(functions.messages.CheckChatInviteRequest(hash=invite_hash))
            if isinstance(invite, types.ChatInviteAlready):
                channel = invite.chat
            else:
                if isinstance(invite, types.ChatInvitePeek):
                    is_broadcast = bool(getattr(invite.chat, "broadcast", False))
                    requires_approval = False
                    paid = False
                elif isinstance(invite, types.ChatInvite):
                    is_broadcast = bool(invite.channel and invite.broadcast)
                    requires_approval = bool(invite.request_needed)
                    paid = getattr(invite, "subscription_pricing", None) is not None
                else:
                    is_broadcast, requires_approval, paid = False, False, False
                if not is_broadcast:
                    raise PrivateChannelError("Ссылка ведёт не на канал")
                if paid:
                    raise PrivateChannelError("Платное приглашение не поддерживается. Пришлите обычную ссылку")
                if requires_approval:
                    raise PrivateChannelError("Нужно одобрение администратора канала. После одобрения повторите запрос")
                try:
                    result = await client(functions.messages.ImportChatInviteRequest(hash=invite_hash))
                except InviteRequestSentError as exc:
                    raise PrivateChannelError("Заявка на вступление отправлена. После одобрения повторите запрос") from exc
                channel = next((chat for chat in result.chats if getattr(chat, "broadcast", False)), None)
            if channel is None or not getattr(channel, "broadcast", False):
                raise PrivateChannelError("Ссылка ведёт не на канал")
            channel_id = utils.get_peer_id(channel)
            full = await client(functions.channels.GetFullChannelRequest(channel=channel))
            raw_members = getattr(full.full_chat, "participants_count", None)
            members = int(raw_members) if raw_members is not None else None
            title = str(getattr(channel, "title", "Закрытый канал"))[:255]
            await collect_one(client, channel, channel_id)
            observed = datetime.now(UTC)
            async with session_factory() as session, session.begin():
                existing = await session.scalar(
                    select(ViewPrivateChannel).where(
                        ViewPrivateChannel.owner_id == owner_id,
                        ViewPrivateChannel.channel_id == channel_id,
                    ).with_for_update()
                )
                if existing is None:
                    session.add(ViewPrivateChannel(
                        owner_id=owner_id,
                        channel_id=channel_id,
                        account_id=account_id,
                        invite_ciphertext=TokenKeyring.parse(get_settings().token_encryption_keys).encrypt(invite_hash),
                        invite_fingerprint=fingerprint,
                        title=title,
                        status="active",
                    ))
                else:
                    existing.account_id = account_id
                    existing.status = "active"
                    existing.title = title
                    existing.invite_fingerprint = fingerprint
                    existing.invite_ciphertext = TokenKeyring.parse(get_settings().token_encryption_keys).encrypt(invite_hash)
                session.add(ViewChannelSnapshot(
                    channel_id=channel_id,
                    username="",
                    title=title,
                    subscriber_count=members,
                    observed_at=observed,
                ))
            return channel_id, title, members, observed
    except (InviteHashExpiredError, InviteHashInvalidError) as exc:
        raise PrivateChannelError("Ссылка истекла или отозвана. Отправьте новое приглашение") from exc
    except ViewPoolError as exc:
        raise PrivateChannelError(str(exc)) from exc


async def analyze_saved_private(account_id: int, channel_id: int) -> tuple[str, int | None, datetime]:
    try:
        async with lease_account(account_id) as (_id, client):
            entity = await client.get_entity(channel_id)
            if not getattr(entity, "broadcast", False) or utils.get_peer_id(entity) != channel_id:
                raise PrivateChannelError("Нет доступа к каналу")
            full = await client(functions.channels.GetFullChannelRequest(channel=entity))
            raw_count = getattr(full.full_chat, "participants_count", None)
            count = int(raw_count) if raw_count is not None else None
            title = str(getattr(entity, "title", "Закрытый канал"))[:255]
            await collect_one(client, entity, channel_id)
            observed = datetime.now(UTC)
            async with session_factory() as session, session.begin():
                session.add(ViewChannelSnapshot(
                    channel_id=channel_id, username="", title=title,
                    subscriber_count=count, observed_at=observed,
                ))
            return title, count, observed
    except ViewPoolError as exc:
        raise PrivateChannelError(str(exc)) from exc
    except ChannelPrivateError as exc:
        async with session_factory() as session, session.begin():
            for row in (await session.scalars(select(ViewPrivateChannel).where(
                ViewPrivateChannel.account_id == account_id,
                ViewPrivateChannel.channel_id == channel_id,
            ))).all():
                row.status = "lost_access"
        raise PrivateChannelError("Аккаунт потерял доступ к каналу") from exc
