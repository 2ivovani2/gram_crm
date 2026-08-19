from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .advertising import choose_free_ad, mark_ad_operation
from .config import get_settings
from .content_compiler import AttachmentSpec, CompiledOperation, compile_step
from .models import (
    Channel,
    Contact,
    ContentAttachment,
    ContentFlow,
    ContentFlowVersion,
    ContentKeyboard,
    ContentKeyboardButton,
    ContentStep,
    DeliveryOperation,
    FlowChannelAssignment,
    FlowDelivery,
    ManagedBot,
)


@dataclass(frozen=True)
class OperationContext:
    operation: DeliveryOperation
    delivery: FlowDelivery
    bot: ManagedBot
    channel: Channel
    contact: Contact


async def keyboard_payload(session: AsyncSession, step_id: int) -> dict[str, Any] | None:
    keyboard = await session.scalar(select(ContentKeyboard).where(ContentKeyboard.step_id == step_id))
    if keyboard is None:
        return None
    buttons = list(
        (
            await session.scalars(
                select(ContentKeyboardButton)
                .where(ContentKeyboardButton.keyboard_id == keyboard.id)
                .order_by(
                    ContentKeyboardButton.row,
                    ContentKeyboardButton.position,
                    ContentKeyboardButton.id,
                )
            )
        ).all()
    )
    rows: list[list[dict[str, str]]] = []
    for button in buttons:
        while len(rows) <= button.row:
            rows.append([])
        rows[button.row].append(
            {
                "text": button.text,
                "action_type": button.action_type,
                "value": button.value,
                "style": button.style,
            }
        )
    return {"kind": keyboard.kind, "settings": keyboard.settings, "rows": rows}


async def compile_preview_operations(
    session: AsyncSession, steps: list[ContentStep]
) -> list[CompiledOperation]:
    """Compile a draft through the exact same path used by production delivery."""
    result: list[CompiledOperation] = []
    for step in steps:
        attachments = list(
            (
                await session.scalars(
                    select(ContentAttachment)
                    .where(ContentAttachment.step_id == step.id)
                    .order_by(ContentAttachment.position, ContentAttachment.id)
                )
            ).all()
        )
        result.extend(
            compile_step(
                step.payload,
                [
                    AttachmentSpec(
                        media_type=item.media_type,
                        storage_key=item.storage_key,
                        original_name=item.original_name,
                        mime_type=item.mime_type,
                        size=item.size,
                        payload=item.payload,
                    )
                    for item in attachments
                ],
                await keyboard_payload(session, step.id),
            )
        )
    return result


async def schedule_content_flow(
    session: AsyncSession,
    *,
    bot: ManagedBot,
    channel_id: int,
    contact_id: int,
    event_key: str,
    kind: str = "welcome",
) -> int | None:
    assignment_exists = exists(
        select(FlowChannelAssignment.id).where(
            FlowChannelAssignment.flow_id == ContentFlow.id,
            FlowChannelAssignment.channel_id == channel_id,
        )
    )
    row = (
        await session.execute(
            select(ContentFlow, ContentFlowVersion)
            .join(
                ContentFlowVersion,
                and_(
                    ContentFlowVersion.flow_id == ContentFlow.id,
                    ContentFlowVersion.status == "published",
                ),
            )
            .where(
                ContentFlow.bot_id == bot.id,
                ContentFlow.kind == kind,
                ContentFlow.is_active.is_(True),
                or_(ContentFlow.assignment_mode == "all", assignment_exists),
            )
            .order_by(
                (ContentFlow.assignment_mode == "selected").desc(),
                ContentFlow.id,
            )
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        return None
    _flow, version = row
    recent = None
    if kind == "welcome":
        recent = await session.scalar(
            select(FlowDelivery.id)
            .join(ContentFlowVersion, ContentFlowVersion.id == FlowDelivery.version_id)
            .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
            .where(
                FlowDelivery.bot_id == bot.id,
                FlowDelivery.channel_id == channel_id,
                FlowDelivery.contact_id == contact_id,
                FlowDelivery.created_at >= datetime.now(UTC) - timedelta(minutes=5),
                FlowDelivery.status != "cancelled",
                ContentFlow.kind == kind,
            )
            .limit(1)
        )
    if recent is not None:
        return int(recent)
    delivery_id = await session.scalar(
        insert(FlowDelivery)
        .values(
            bot_id=bot.id,
            channel_id=channel_id,
            contact_id=contact_id,
            version_id=version.id,
            event_key=event_key,
            status="scheduled",
        )
        .on_conflict_do_nothing(constraint="uq_flow_delivery_bot_event")
        .returning(FlowDelivery.id)
    )
    if delivery_id is None:
        existing = await session.scalar(
            select(FlowDelivery.id).where(
                FlowDelivery.bot_id == bot.id,
                FlowDelivery.event_key == event_key,
            )
        )
        return int(existing) if existing is not None else None
    if kind == "farewell":
        contact = await session.get(Contact, contact_id)
        if contact is None or not contact.bot_started:
            await session.execute(
                update(FlowDelivery)
                .where(FlowDelivery.id == delivery_id)
                .values(status="unreachable", completed_at=datetime.now(UTC))
            )
            return int(delivery_id)
    steps = list(
        (
            await session.scalars(
                select(ContentStep)
                .where(ContentStep.version_id == version.id)
                .order_by(ContentStep.position, ContentStep.id)
            )
        ).all()
    )
    due_at = datetime.now(UTC) + timedelta(seconds=version.first_delay_seconds)
    previous_operation_id: int | None = None
    operation_position = 0
    for step in steps:
        attachments = list(
            (
                await session.scalars(
                    select(ContentAttachment)
                    .where(ContentAttachment.step_id == step.id)
                    .order_by(ContentAttachment.position, ContentAttachment.id)
                )
            ).all()
        )
        compiled = compile_step(
            step.payload,
            [
                AttachmentSpec(
                    media_type=item.media_type,
                    storage_key=item.storage_key,
                    original_name=item.original_name,
                    mime_type=item.mime_type,
                    size=item.size,
                    payload=item.payload,
                )
                for item in attachments
            ],
            await keyboard_payload(session, step.id),
        )
        for item in compiled:
            operation = DeliveryOperation(
                flow_delivery_id=delivery_id,
                step_id=step.id,
                depends_on_operation_id=previous_operation_id,
                position=operation_position,
                operation_type=item.operation_type,
                payload=item.payload,
                media=item.media,
                status="scheduled",
                due_at=due_at,
            )
            session.add(operation)
            await session.flush()
            previous_operation_id = operation.id
            operation_position += 1
        due_at += timedelta(seconds=step.delay_after_seconds)
    if operation_position == 0:
        await session.execute(
            update(FlowDelivery)
            .where(FlowDelivery.id == delivery_id)
            .values(status="failed", completed_at=datetime.now(UTC))
        )
    elif kind == "welcome":
        scheduled_ad = await choose_free_ad(
            session,
            owner_id=bot.owner_id,
            bot_id=bot.id,
            channel_id=channel_id,
            contact_id=contact_id,
            flow_delivery_id=delivery_id,
            public_service_base_url=get_settings().public_service_base_url,
        )
        if scheduled_ad is not None:
            operation = DeliveryOperation(
                flow_delivery_id=delivery_id,
                step_id=steps[-1].id,
                depends_on_operation_id=previous_operation_id,
                position=operation_position,
                operation_type="text",
                payload=scheduled_ad.payload,
                media=[],
                status="scheduled",
                due_at=due_at,
            )
            session.add(operation)
            await session.flush()
            scheduled_ad.impression.operation_id = operation.id
    return int(delivery_id)


async def claim_operation_batch(
    session: AsyncSession, *, worker_id: str, limit: int, lease_seconds: int
) -> list[DeliveryOperation]:
    now = datetime.now(UTC)
    dependency = aliased(DeliveryOperation)
    async with session.begin():
        runnable = (
            select(
                DeliveryOperation.id,
                FlowDelivery.bot_id,
                func.row_number()
                .over(
                    partition_by=FlowDelivery.bot_id,
                    order_by=(DeliveryOperation.due_at, DeliveryOperation.id),
                )
                .label("bot_rank"),
            )
            .join(FlowDelivery, FlowDelivery.id == DeliveryOperation.flow_delivery_id)
            .outerjoin(dependency, dependency.id == DeliveryOperation.depends_on_operation_id)
            .where(
                or_(
                    and_(
                        DeliveryOperation.status.in_(("scheduled", "retry")),
                        DeliveryOperation.due_at <= now,
                        or_(
                            DeliveryOperation.depends_on_operation_id.is_(None),
                            dependency.status == "sent",
                        ),
                    ),
                    and_(
                        DeliveryOperation.status == "processing",
                        DeliveryOperation.lease_expires_at < now,
                    ),
                )
            )
            .subquery()
        )
        operations = list(
            (
                await session.scalars(
                    select(DeliveryOperation)
                    .join(runnable, runnable.c.id == DeliveryOperation.id)
                    .where(runnable.c.bot_rank <= 3)
                    .order_by(DeliveryOperation.due_at, DeliveryOperation.id)
                    .limit(limit)
                    .with_for_update(of=DeliveryOperation, skip_locked=True)
                )
            ).all()
        )
        lease_until = now + timedelta(seconds=lease_seconds)
        for operation in operations:
            operation.status = "processing"
            operation.lease_owner = worker_id
            operation.lease_expires_at = lease_until
            operation.attempts += 1
            await session.execute(
                update(FlowDelivery)
                .where(
                    FlowDelivery.id == operation.flow_delivery_id,
                    FlowDelivery.status == "scheduled",
                )
                .values(status="processing")
            )
    return operations


async def load_operation_context(session: AsyncSession, operation_id: int) -> OperationContext | None:
    operation = await session.get(DeliveryOperation, operation_id)
    if operation is None:
        return None
    delivery = await session.get(FlowDelivery, operation.flow_delivery_id)
    if delivery is None:
        return None
    bot = await session.get(ManagedBot, delivery.bot_id)
    channel = await session.get(Channel, delivery.channel_id)
    contact = await session.get(Contact, delivery.contact_id)
    if bot is None or channel is None or contact is None:
        return None
    return OperationContext(operation, delivery, bot, channel, contact)


async def defer_operation(
    session: AsyncSession,
    operation_id: int,
    worker_id: str,
    *,
    delay_seconds: int,
    error: str,
) -> None:
    await session.execute(
        update(DeliveryOperation)
        .where(
            DeliveryOperation.id == operation_id,
            DeliveryOperation.lease_owner == worker_id,
        )
        .values(
            status="retry",
            due_at=datetime.now(UTC) + timedelta(seconds=max(1, delay_seconds)),
            lease_owner=None,
            lease_expires_at=None,
            error=error[:500],
        )
    )
    await session.commit()


async def finish_operation(
    session: AsyncSession,
    operation_id: int,
    worker_id: str,
    *,
    success: bool,
    error: str = "",
) -> None:
    operation = await session.scalar(
        select(DeliveryOperation)
        .where(
            DeliveryOperation.id == operation_id,
            DeliveryOperation.lease_owner == worker_id,
        )
        .with_for_update()
    )
    if operation is None:
        return
    operation.status = "sent" if success else "failed"
    operation.sent_at = datetime.now(UTC) if success else None
    operation.lease_owner = None
    operation.lease_expires_at = None
    operation.error = error[:500]
    await mark_ad_operation(
        session,
        operation.id,
        status="sent" if success else "failed",
        error=error,
    )
    await session.flush()
    if not success:
        await session.execute(
            update(DeliveryOperation)
            .where(
                DeliveryOperation.flow_delivery_id == operation.flow_delivery_id,
                DeliveryOperation.status.in_(("scheduled", "retry")),
            )
            .values(status="cancelled", error="dependency_failed")
        )
        cancelled_ad_operations = select(DeliveryOperation.id).where(
            DeliveryOperation.flow_delivery_id == operation.flow_delivery_id,
            DeliveryOperation.status == "cancelled",
        )
        cancelled_ids = list(await session.scalars(cancelled_ad_operations))
        for cancelled_id in cancelled_ids:
            await mark_ad_operation(
                session,
                cancelled_id,
                status="cancelled",
                error="dependency_failed",
            )
    count_rows = (
        await session.execute(
            select(DeliveryOperation.status, func.count(DeliveryOperation.id))
            .where(DeliveryOperation.flow_delivery_id == operation.flow_delivery_id)
            .group_by(DeliveryOperation.status)
        )
    ).all()
    counts: dict[str, int] = {str(status): int(total) for status, total in count_rows}
    pending = sum(counts.get(item, 0) for item in ("scheduled", "retry", "processing"))
    if pending == 0:
        if counts.get("failed", 0):
            final_status = "partial" if counts.get("sent", 0) else "failed"
        else:
            final_status = "completed"
        await session.execute(
            update(FlowDelivery)
            .where(FlowDelivery.id == operation.flow_delivery_id)
            .values(status=final_status, completed_at=datetime.now(UTC))
        )
    await session.commit()
