from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .content_compiler import AttachmentSpec, CompiledOperation, compile_step
from .content_service import ContentValidationError, validate_step_keyboard
from .metrics import BROADCAST_RECIPIENTS
from .models import (
    Broadcast,
    BroadcastRecipient,
    Channel,
    Contact,
    EditorMedia,
    ManagedBot,
)
from .rich_text import validate_rich_text


def _attachment(item: dict[str, Any]) -> AttachmentSpec:
    try:
        return AttachmentSpec(
            media_type=str(item["media_type"]),
            storage_key=str(item["storage_key"]),
            original_name=str(item.get("original_name") or ""),
            mime_type=str(item.get("mime_type") or ""),
            size=int(item.get("size") or 0),
            payload=item.get("payload") if isinstance(item.get("payload"), dict) else {},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContentValidationError("Некорректное вложение рассылки") from exc


def validate_broadcast_content(content: dict[str, Any]) -> dict[str, Any]:
    payload = content.get("payload")
    attachments = content.get("attachments", [])
    keyboard = content.get("keyboard")
    if not isinstance(payload, dict) or not isinstance(attachments, list):
        raise ContentValidationError("Некорректное содержимое рассылки")
    if len(attachments) > 5:
        raise ContentValidationError("Можно добавить не более пяти файлов")
    validate_rich_text(payload)
    normalized_keyboard = validate_step_keyboard(keyboard) if isinstance(keyboard, dict) else None
    if normalized_keyboard and any(
        button["action_type"] == "subscribe"
        for row in normalized_keyboard["rows"]
        for button in row
    ):
        raise ContentValidationError("Кнопка добавления в базу доступна только в приветствии")
    operations = compile_step(
        payload,
        [_attachment(item) for item in attachments],
        normalized_keyboard,
    )
    if not operations or not any(
        operation.media or str(operation.payload.get("text") or operation.payload.get("caption") or "").strip()
        for operation in operations
    ):
        raise ContentValidationError("Добавьте текст или файл")
    return {"payload": payload, "attachments": attachments, "keyboard": normalized_keyboard}


async def verify_broadcast_media(
    session: AsyncSession, owner_id: int, content: dict[str, Any]
) -> None:
    raw_ids = [item.get("media_id") for item in content.get("attachments", []) if item.get("media_id")]
    if not raw_ids:
        return
    try:
        ids = [uuid.UUID(str(value)) for value in raw_ids]
    except ValueError as exc:
        raise ContentValidationError("Некорректный идентификатор файла") from exc
    owned = set(
        await session.scalars(
            select(EditorMedia.id).where(EditorMedia.owner_id == owner_id, EditorMedia.id.in_(ids))
        )
    )
    if len(owned) != len(set(ids)):
        raise ContentValidationError("Один из файлов недоступен")


async def save_broadcast(
    session: AsyncSession,
    *,
    owner_id: int,
    bot_id: int,
    name: str,
    content: dict[str, Any],
    source_channel_id: int | None,
    idempotency_key: str,
) -> Broadcast:
    bot = await session.scalar(
        select(ManagedBot).where(
            ManagedBot.id == bot_id,
            ManagedBot.owner_id == owner_id,
            ManagedBot.is_active.is_(True),
        )
    )
    if bot is None:
        raise ContentValidationError("Бот недоступен")
    if source_channel_id is not None and not await session.scalar(
        select(Channel.id).where(
            Channel.id == source_channel_id,
            Channel.bot_id == bot_id,
            Channel.is_active.is_(True),
        )
    ):
        raise ContentValidationError("Канал-источник недоступен")
    normalized = validate_broadcast_content(content)
    await verify_broadcast_media(session, owner_id, normalized)
    existing = await session.scalar(
        select(Broadcast).where(
            Broadcast.owner_id == owner_id, Broadcast.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        return existing
    row = Broadcast(
        owner_id=owner_id,
        bot_id=bot_id,
        source_channel_id=source_channel_id,
        name=name.strip()[:128],
        content=normalized,
        idempotency_key=idempotency_key,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def launch_broadcast(
    session: AsyncSession,
    *,
    owner_id: int,
    broadcast_id: int,
    scheduled_at: datetime | None,
) -> tuple[Broadcast, int]:
    row = await session.scalar(
        select(Broadcast)
        .where(Broadcast.id == broadcast_id, Broadcast.owner_id == owner_id)
        .with_for_update()
    )
    if row is None:
        raise ContentValidationError("Рассылка не найдена")
    if row.status != "draft":
        count = await session.scalar(
            select(func.count()).select_from(BroadcastRecipient).where(
                BroadcastRecipient.broadcast_id == row.id
            )
        )
        return row, int(count or 0)
    due_at = scheduled_at or datetime.now(UTC)
    contacts = select(Contact.id).where(
        Contact.bot_id == row.bot_id,
        Contact.bot_started.is_(True),
        Contact.consent_status == "active",
    )
    if row.source_channel_id is not None:
        contacts = contacts.where(Contact.source_channel_id == row.source_channel_id)
    contact_ids = list(await session.scalars(contacts))
    if not contact_ids:
        raise ContentValidationError("В выбранной аудитории пока нет активных подписчиков")
    await session.execute(
        insert(BroadcastRecipient).values(
            [
                {
                    "broadcast_id": row.id,
                    "contact_id": contact_id,
                    "status": "scheduled",
                    "due_at": due_at,
                }
                for contact_id in contact_ids
            ]
        )
    )
    row.status = "scheduled"
    row.scheduled_at = due_at
    await session.commit()
    return row, len(contact_ids)


async def cancel_broadcast(session: AsyncSession, *, owner_id: int, broadcast_id: int) -> None:
    row = await session.scalar(
        select(Broadcast).where(Broadcast.id == broadcast_id, Broadcast.owner_id == owner_id).with_for_update()
    )
    if row is None:
        raise ContentValidationError("Рассылка не найдена")
    if row.status in {"completed", "failed"}:
        raise ContentValidationError("Завершённую рассылку отменить нельзя")
    row.status = "cancelled"
    row.completed_at = datetime.now(UTC)
    await session.execute(
        update(BroadcastRecipient)
        .where(
            BroadcastRecipient.broadcast_id == row.id,
            BroadcastRecipient.status.in_(("scheduled", "retry")),
        )
        .values(status="cancelled", lease_owner=None, lease_expires_at=None)
    )
    await session.commit()


@dataclass(frozen=True)
class BroadcastContext:
    recipient: BroadcastRecipient
    broadcast: Broadcast
    bot: ManagedBot
    contact: Contact
    operations: list[CompiledOperation]


async def claim_broadcast_recipients(
    session: AsyncSession, *, worker_id: str, limit: int, lease_seconds: int
) -> list[BroadcastRecipient]:
    now = datetime.now(UTC)
    rows = list(
        await session.scalars(
            select(BroadcastRecipient)
            .join(Broadcast, Broadcast.id == BroadcastRecipient.broadcast_id)
            .where(
                Broadcast.status.in_(("scheduled", "running")),
                or_(
                    and_(
                        BroadcastRecipient.status.in_(("scheduled", "retry")),
                        BroadcastRecipient.due_at <= now,
                    ),
                    and_(
                        BroadcastRecipient.status == "processing",
                        BroadcastRecipient.lease_expires_at < now,
                    ),
                ),
            )
            .order_by(BroadcastRecipient.due_at, BroadcastRecipient.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    )
    if rows:
        await session.execute(
            update(Broadcast)
            .where(Broadcast.id.in_({row.broadcast_id for row in rows}), Broadcast.status == "scheduled")
            .values(status="running", started_at=now)
        )
    for row in rows:
        row.status = "processing"
        row.attempts += 1
        row.lease_owner = worker_id
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
    await session.commit()
    return rows


async def load_broadcast_context(
    session: AsyncSession, recipient_id: int
) -> BroadcastContext | None:
    recipient = await session.get(BroadcastRecipient, recipient_id)
    if recipient is None:
        return None
    broadcast = await session.get(Broadcast, recipient.broadcast_id)
    contact = await session.get(Contact, recipient.contact_id)
    if broadcast is None or contact is None:
        return None
    bot = await session.get(ManagedBot, broadcast.bot_id)
    if bot is None:
        return None
    content = validate_broadcast_content(broadcast.content)
    keyboard = content.get("keyboard")
    if keyboard:
        keyboard = {
            **keyboard,
            "rows": [
                *keyboard["rows"],
                [{"text": "Отписаться", "action_type": "callback", "value": "consent:stop", "style": "danger"}],
            ],
        }
    else:
        keyboard = {
            "kind": "inline",
            "rows": [[{"text": "Отписаться", "action_type": "callback", "value": "consent:stop", "style": "danger"}]],
        }
    operations = compile_step(
        content["payload"],
        [_attachment(item) for item in content["attachments"]],
        keyboard,
    )
    return BroadcastContext(recipient, broadcast, bot, contact, operations)


async def defer_broadcast_recipient(
    session: AsyncSession, recipient_id: int, worker_id: str, *, delay_seconds: int, error: str
) -> None:
    await session.execute(
        update(BroadcastRecipient)
        .where(BroadcastRecipient.id == recipient_id, BroadcastRecipient.lease_owner == worker_id)
        .values(
            status="retry",
            due_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
            lease_owner=None,
            lease_expires_at=None,
            error=error[:64],
        )
    )
    await session.commit()


async def finish_broadcast_recipient(
    session: AsyncSession,
    recipient_id: int,
    worker_id: str,
    *,
    status: str,
    error: str = "",
) -> None:
    recipient = await session.scalar(
        select(BroadcastRecipient)
        .where(BroadcastRecipient.id == recipient_id, BroadcastRecipient.lease_owner == worker_id)
        .with_for_update()
    )
    if recipient is None:
        return
    recipient.status = status
    recipient.sent_at = datetime.now(UTC) if status == "sent" else None
    recipient.error = error[:64]
    recipient.lease_owner = None
    recipient.lease_expires_at = None
    BROADCAST_RECIPIENTS.labels(status).inc()
    if status == "blocked":
        await session.execute(
            update(Contact)
            .where(Contact.id == recipient.contact_id)
            .values(consent_status="blocked", delivery_status="dead", last_error="bot_blocked_by_user")
        )
    open_count = await session.scalar(
        select(func.count()).select_from(BroadcastRecipient).where(
            BroadcastRecipient.broadcast_id == recipient.broadcast_id,
            BroadcastRecipient.status.in_(("scheduled", "processing", "retry")),
            BroadcastRecipient.id != recipient.id,
        )
    )
    if not open_count:
        await session.execute(
            update(Broadcast)
            .where(Broadcast.id == recipient.broadcast_id)
            .values(status="completed", completed_at=datetime.now(UTC))
        )
    await session.commit()
