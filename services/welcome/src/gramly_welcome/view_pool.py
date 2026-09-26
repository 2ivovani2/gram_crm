"""Encrypted, leased user sessions for GramlyView MTProto reads.

An uploaded SQLite session is a credential. It is never exposed by a read API,
logged, or shared between concurrently running Telethon clients.
"""

from __future__ import annotations

import asyncio
import base64
import os
import sqlite3
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select
from telethon import TelegramClient

from .config import get_settings
from .crypto import TokenKeyring
from .db import session_factory
from .models import ViewMtprotoAccount, ViewPrivateChannel

MIN_HEALTHY_ACCOUNTS = 5
MAX_ACCOUNTS = 25
MAX_PRIVATE_CHANNELS_PER_ACCOUNT = 200
MAX_SESSION_BYTES = 1_048_576
HEALTH_TTL = timedelta(minutes=10)
LEASE_TIME = timedelta(minutes=4)


class ViewPoolError(ValueError):
    pass


def _keyring() -> TokenKeyring:
    return TokenKeyring.parse(get_settings().token_encryption_keys)


def seal_session(raw: bytes) -> str:
    return _keyring().encrypt(base64.b64encode(raw).decode("ascii"))


def open_session(value: str) -> bytes:
    return base64.b64decode(_keyring().decrypt(value), validate=True)


def validate_session_file(raw: bytes) -> None:
    if len(raw) < 100 or len(raw) > MAX_SESSION_BYTES or not raw.startswith(b"SQLite format 3\x00"):
        raise ViewPoolError("Нужен Telethon .session файл размером не более 1 МБ")
    with tempfile.TemporaryDirectory(prefix="gramly-view-check-") as directory:
        path = Path(directory) / "source.session"
        path.write_bytes(raw)
        os.chmod(path, 0o600)
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
            try:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"sessions", "version"}.issubset(tables):
                    raise ViewPoolError("Формат Telethon .session не распознан")
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ViewPoolError("Файл сессии повреждён")
            finally:
                db.close()
        except sqlite3.DatabaseError as exc:
            raise ViewPoolError("Файл сессии повреждён") from exc


@asynccontextmanager
async def _client_for_bytes(raw: bytes) -> AsyncIterator[tuple[TelegramClient, Path]]:
    settings = get_settings()
    if not settings.view_mtproto_api_id or not settings.view_mtproto_api_hash:
        raise ViewPoolError("MTProto API ID и hash не настроены на сервере")
    with tempfile.TemporaryDirectory(prefix="gramly-view-session-") as directory:
        path = Path(directory) / "account.session"
        path.write_bytes(raw)
        os.chmod(path, 0o600)
        client = TelegramClient(str(path), settings.view_mtproto_api_id, settings.view_mtproto_api_hash)
        try:
            await client.connect()
            yield client, path
        finally:
            await client.disconnect()


async def inspect_session(raw: bytes) -> tuple[int, str, bytes]:
    validate_session_file(raw)
    async with _client_for_bytes(raw) as (client, path):
        if not await client.is_user_authorized():
            raise ViewPoolError("Сессия Telegram не авторизована")
        user = await client.get_me()
        if user is None or getattr(user, "bot", False):
            raise ViewPoolError("Нужна сессия пользовательского аккаунта")
        label = (getattr(user, "username", None) or getattr(user, "first_name", None) or str(user.id))[:128]
        await client.disconnect()
        updated = path.read_bytes()
    return int(user.id), label, updated


async def healthy_count() -> int:
    now = datetime.now(UTC)
    async with session_factory() as session:
        return int(
            await session.scalar(
                select(func.count(ViewMtprotoAccount.id)).where(
                    ViewMtprotoAccount.status == "healthy",
                    ViewMtprotoAccount.disabled.is_(False),
                    ViewMtprotoAccount.checked_at >= now - HEALTH_TTL,
                    or_(
                        ViewMtprotoAccount.cooldown_until.is_(None), ViewMtprotoAccount.cooldown_until <= now
                    ),
                )
            )
            or 0
        )


async def analytics_available() -> bool:
    settings = get_settings()
    return bool(settings.view_mtproto_api_id and settings.view_mtproto_api_hash) and (
        await healthy_count() >= MIN_HEALTHY_ACCOUNTS
    )


@asynccontextmanager
async def lease_account(
    account_id: int | None = None, *, for_private_join: bool = False
) -> AsyncIterator[tuple[int, TelegramClient]]:
    from telethon.errors import FloodWaitError

    now = datetime.now(UTC)
    lease_id = uuid.uuid4().hex
    async with session_factory() as session, session.begin():
        assignments = (
            select(ViewPrivateChannel.account_id, func.count(ViewPrivateChannel.id).label("total"))
            .group_by(ViewPrivateChannel.account_id)
            .subquery()
        )
        query = select(ViewMtprotoAccount).where(
            ViewMtprotoAccount.status == "healthy",
            ViewMtprotoAccount.disabled.is_(False),
            ViewMtprotoAccount.checked_at >= now - HEALTH_TTL,
            or_(ViewMtprotoAccount.cooldown_until.is_(None), ViewMtprotoAccount.cooldown_until <= now),
            or_(ViewMtprotoAccount.lease_until.is_(None), ViewMtprotoAccount.lease_until < now),
        )
        if account_id is not None:
            query = query.where(ViewMtprotoAccount.id == account_id)
        elif for_private_join:
            query = (
                query.outerjoin(assignments, assignments.c.account_id == ViewMtprotoAccount.id)
                .where(
                    func.coalesce(assignments.c.total, 0) < MAX_PRIVATE_CHANNELS_PER_ACCOUNT,
                    or_(
                        ViewMtprotoAccount.last_join_at.is_(None),
                        ViewMtprotoAccount.last_join_at < now - timedelta(minutes=1),
                    ),
                )
                .order_by(
                    func.coalesce(assignments.c.total, 0), ViewMtprotoAccount.last_used_at.asc().nullsfirst()
                )
            )
        else:
            query = query.order_by(ViewMtprotoAccount.last_used_at.asc().nullsfirst())
        account = await session.scalar(
            query.with_for_update(of=ViewMtprotoAccount, skip_locked=True).limit(1)
        )
        if account is None:
            raise ViewPoolError("Нет свободного аккаунта с доступом к каналу")
        account.lease_owner = lease_id
        account.lease_until = now + LEASE_TIME
        account.last_used_at = now
        if for_private_join:
            account.last_join_at = now
        encrypted = account.encrypted_session
        selected_id = account.id
    try:
        unauthorized = False
        async with _client_for_bytes(open_session(encrypted)) as (client, path):
            if not await client.is_user_authorized():
                unauthorized = True
                raise ViewPoolError("Сессия Telegram больше не авторизована")
            try:
                yield selected_id, client
            finally:
                await client.disconnect()
                updated = seal_session(path.read_bytes())
                async with session_factory() as session, session.begin():
                    row = await session.get(ViewMtprotoAccount, selected_id, with_for_update=True)
                    if row is not None and row.lease_owner == lease_id:
                        row.encrypted_session = updated
    except FloodWaitError as exc:
        async with session_factory() as session, session.begin():
            row = await session.get(ViewMtprotoAccount, selected_id, with_for_update=True)
            if row is not None and row.lease_owner == lease_id:
                row.status = "cooldown"
                row.last_error = "flood_wait"
                row.cooldown_until = datetime.now(UTC) + timedelta(seconds=int(exc.seconds))
        raise ViewPoolError("Telegram временно ограничил аккаунт сбора") from exc
    except ViewPoolError:
        if unauthorized:
            async with session_factory() as session, session.begin():
                row = await session.get(ViewMtprotoAccount, selected_id, with_for_update=True)
                if row is not None and row.lease_owner == lease_id:
                    row.status = "revoked"
                    row.last_error = "unauthorized"
                    row.checked_at = datetime.now(UTC)
        raise
    finally:
        async with session_factory() as session, session.begin():
            row = await session.get(ViewMtprotoAccount, selected_id, with_for_update=True)
            if row is not None and row.lease_owner == lease_id:
                row.lease_owner = None
                row.lease_until = None


async def check_all_accounts() -> None:
    """Probe each account without letting a scheduled probe race an active read."""
    from telethon.errors import FloodWaitError

    async with session_factory() as session:
        ids = (await session.scalars(select(ViewMtprotoAccount.id).order_by(ViewMtprotoAccount.id))).all()
    semaphore = asyncio.Semaphore(5)

    async def check_one(account_id: int) -> None:
        async with semaphore:
            now = datetime.now(UTC)
            token = uuid.uuid4().hex
            async with session_factory() as session, session.begin():
                row = await session.scalar(
                    select(ViewMtprotoAccount).where(ViewMtprotoAccount.id == account_id).with_for_update()
                )
                if row is None or row.disabled or (row.lease_until and row.lease_until > now):
                    return
                row.lease_owner = token
                row.lease_until = now + LEASE_TIME
                encrypted = row.encrypted_session
                expected_id = row.telegram_id
            status, error, cooldown, updated = "unavailable", "probe_interrupted", None, None
            try:
                async with asyncio.timeout(15):
                    async with _client_for_bytes(open_session(encrypted)) as (client, path):
                        if not await client.is_user_authorized():
                            status, error = "revoked", "unauthorized"
                        else:
                            me = await client.get_me()
                            if me is None or int(me.id) != expected_id:
                                status, error = "revoked", "identity_changed"
                            else:
                                status, error = "healthy", ""
                        await client.disconnect()
                        updated = seal_session(path.read_bytes())
            except FloodWaitError as exc:
                status, error = "cooldown", "flood_wait"
                cooldown = datetime.now(UTC) + timedelta(seconds=int(exc.seconds))
            except Exception as exc:
                status, error = "unavailable", type(exc).__name__[:64]
            finally:
                async with session_factory() as session, session.begin():
                    row = await session.get(ViewMtprotoAccount, account_id, with_for_update=True)
                    if row is not None and row.lease_owner == token:
                        row.status = status
                        row.last_error = error
                        row.checked_at = datetime.now(UTC)
                        row.cooldown_until = cooldown
                        if updated is not None:
                            row.encrypted_session = updated
                        row.lease_owner = None
                        row.lease_until = None

    await asyncio.gather(*(check_one(account_id) for account_id in ids))
    await refresh_pool_alert()


async def refresh_pool_alert() -> None:
    from .models import Owner, ViewPoolAlert

    count = await healthy_count()
    settings = get_settings()
    api_configured = bool(settings.view_mtproto_api_id and settings.view_mtproto_api_hash)
    below = count < MIN_HEALTHY_ACCOUNTS or not api_configured
    target: int | None = None
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        alert = await session.get(ViewPoolAlert, 1, with_for_update=True)
        if alert is None:
            alert = ViewPoolAlert(id=1)
            session.add(alert)
        changed = alert.below_threshold != below
        reminder_due = (
            below and (alert.last_notified_at is None or alert.last_notified_at < now - timedelta(hours=6))
        ) or (not below and alert.last_notified_at is None)
        if changed or reminder_due:
            alert.below_threshold = below
            alert.updated_at = now
            alert.last_notified_at = now
            if alert.recipient_owner_id:
                target = await session.scalar(
                    select(Owner.telegram_id).where(Owner.id == alert.recipient_owner_id)
                )
    if target is not None:
        from .view_bot import _send

        try:
            await _send(
                target,
                f"<b>GramlyView: {'аналитика приостановлена' if below else 'аналитика восстановлена'}</b>\n"
                f"Рабочих MTProto-аккаунтов: {count} из необходимого минимума {MIN_HEALTHY_ACCOUNTS}.\n"
                + ("Настройте API ID/hash на сервере.\n" if not api_configured else "")
                + (
                    "Добавьте или восстановите аккаунты в hello-admin."
                    if below
                    else "Новые запросы снова обрабатываются."
                ),
            )
        except Exception:
            # The persistent admin status remains visible. Retry the alert on
            # the next probe rather than losing a failed notification.
            async with session_factory() as session, session.begin():
                alert = await session.get(ViewPoolAlert, 1, with_for_update=True)
                if alert is not None:
                    alert.last_notified_at = None
