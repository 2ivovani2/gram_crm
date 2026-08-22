from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from .join_request_policy import JOIN_REQUEST_MAX_TIMELINE_SECONDS
from .models import (
    Channel,
    ContentAttachment,
    ContentFlow,
    ContentFlowVersion,
    ContentKeyboard,
    ContentKeyboardButton,
    ContentStep,
    EventLog,
    FlowChannelAssignment,
    ManagedBot,
)


def flow_timeline_seconds(version: ContentFlowVersion, steps: list[ContentStep]) -> int:
    """Return the time at which the final flow step becomes runnable."""
    return int(version.first_delay_seconds) + sum(
        int(step.delay_after_seconds) for step in steps[:-1]
    )


class ContentValidationError(ValueError):
    pass


MAX_KEYBOARD_ROWS = 15
MAX_KEYBOARD_BUTTONS_PER_ROW = 3
MAX_KEYBOARD_BUTTON_TEXT_LENGTH = 128
MAX_KEYBOARD_URL_LENGTH = 1024


def _validate_button_url(value: str, *, row: int, button: int) -> None:
    location = f"Ряд {row}, кнопка {button}"
    if len(value) > MAX_KEYBOARD_URL_LENGTH:
        raise ContentValidationError(
            f"{location}: ссылка длиннее {MAX_KEYBOARD_URL_LENGTH} символов"
        )
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise ContentValidationError(f"{location}: ссылка не должна содержать пробелы")
    if "\\" in value:
        raise ContentValidationError(f"{location}: ссылка содержит недопустимый символ \\")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port  # Access validates a malformed port.
    except ValueError as exc:
        raise ContentValidationError(f"{location}: некорректная ссылка") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ContentValidationError(
            f"{location}: ссылка должна начинаться с http:// или https://"
        )
    if not parsed.netloc or not hostname:
        raise ContentValidationError(f"{location}: в ссылке отсутствует адрес сайта")
    try:
        encoded_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ContentValidationError(f"{location}: некорректный адрес сайта") from exc
    labels = encoded_hostname.rstrip(".").split(".")
    if not labels or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not all(character.isalnum() or character == "-" for character in label)
        for label in labels
    ):
        raise ContentValidationError(f"{location}: некорректный адрес сайта")


def validate_step_keyboard(keyboard: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a keyboard before replacing persisted data."""

    kind = str(keyboard.get("kind") or "")
    if kind not in {"inline", "reply"}:
        raise ContentValidationError("Тип клавиатуры должен быть inline или reply")
    rows = keyboard.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ContentValidationError("Добавьте хотя бы одну кнопку")
    if len(rows) > MAX_KEYBOARD_ROWS:
        raise ContentValidationError(f"Можно добавить не более {MAX_KEYBOARD_ROWS} рядов")

    normalized_rows: list[list[dict[str, str]]] = []
    for row_index, row in enumerate(rows, start=1):
        if not isinstance(row, list) or not row:
            raise ContentValidationError(f"Ряд {row_index}: добавьте хотя бы одну кнопку")
        if len(row) > MAX_KEYBOARD_BUTTONS_PER_ROW:
            raise ContentValidationError(
                f"Ряд {row_index}: можно добавить не более "
                f"{MAX_KEYBOARD_BUTTONS_PER_ROW} кнопок"
            )
        normalized_row: list[dict[str, str]] = []
        for button_index, raw in enumerate(row, start=1):
            if not isinstance(raw, dict):
                raise ContentValidationError(
                    f"Ряд {row_index}, кнопка {button_index}: неверный формат"
                )
            location = f"Ряд {row_index}, кнопка {button_index}"
            text_value = str(raw.get("text") or "").strip()
            action = str(raw.get("action_type") or ("text" if kind == "reply" else "callback"))
            value = str(raw.get("value") or text_value).strip()
            style = str(raw.get("style") or "default")
            if not text_value:
                raise ContentValidationError(f"{location}: укажите название")
            if len(text_value) > MAX_KEYBOARD_BUTTON_TEXT_LENGTH:
                raise ContentValidationError(
                    f"{location}: название длиннее {MAX_KEYBOARD_BUTTON_TEXT_LENGTH} символов"
                )
            if action not in {"url", "callback", "text"}:
                raise ContentValidationError(f"{location}: неподдерживаемое действие")
            if kind == "reply" and action != "text":
                raise ContentValidationError(f"{location}: reply-кнопка поддерживает только текст")
            if kind == "inline" and action == "text":
                raise ContentValidationError(f"{location}: inline-кнопке нужна ссылка или callback")
            if action == "callback" and (not value or len(value.encode()) > 64):
                raise ContentValidationError(f"{location}: callback должен занимать от 1 до 64 байт")
            if action == "url":
                _validate_button_url(value, row=row_index, button=button_index)
            if style not in {"default", "primary", "success", "danger"}:
                raise ContentValidationError(f"{location}: неподдерживаемый стиль")
            normalized_row.append(
                {"text": text_value, "action_type": action, "value": value, "style": style}
            )
        normalized_rows.append(normalized_row)
    return {
        "kind": kind,
        "settings": keyboard.get("settings") if isinstance(keyboard.get("settings"), dict) else {},
        "rows": normalized_rows,
    }


@dataclass(frozen=True)
class DraftSnapshot:
    flow: ContentFlow
    version: ContentFlowVersion
    steps: list[ContentStep]


async def _owned_flow(
    session: AsyncSession, owner_id: int, flow_id: int, *, lock: bool = False
) -> ContentFlow:
    statement = (
        select(ContentFlow)
        .join(ManagedBot, ManagedBot.id == ContentFlow.bot_id)
        .where(
            ContentFlow.id == flow_id,
            ManagedBot.owner_id == owner_id,
            ManagedBot.is_active.is_(True),
        )
    )
    if lock:
        statement = statement.with_for_update(of=ContentFlow)
    flow = await session.scalar(statement)
    if flow is None:
        raise ContentValidationError("Content flow was not found")
    return flow


async def ensure_default_content_flow(
    session: AsyncSession, bot_id: int, owner_id: int, *, kind: str
) -> ContentFlow:
    if kind not in {"welcome", "farewell"}:
        raise ContentValidationError("Unsupported content flow kind")
    owned = await session.scalar(
        select(ManagedBot.id).where(
            ManagedBot.id == bot_id,
            ManagedBot.owner_id == owner_id,
            ManagedBot.is_active.is_(True),
        )
    )
    if owned is None:
        raise ContentValidationError("Bot was not found")
    name = "Приветствие" if kind == "welcome" else "Прощание"
    flow_id = await session.scalar(
        insert(ContentFlow)
        .values(
            bot_id=bot_id,
            name=name,
            kind=kind,
            assignment_mode="all",
            is_active=True,
        )
        .on_conflict_do_nothing(constraint="uq_content_flow_bot_kind_name")
        .returning(ContentFlow.id)
    )
    if flow_id is None:
        flow_id = await session.scalar(
            select(ContentFlow.id).where(
                ContentFlow.bot_id == bot_id,
                ContentFlow.kind == kind,
                ContentFlow.name == name,
            )
        )
    flow = await session.get(ContentFlow, flow_id)
    if flow is None:
        raise RuntimeError("Content flow upsert failed")
    await session.commit()
    return flow


async def ensure_default_welcome_flow(session: AsyncSession, bot_id: int, owner_id: int) -> ContentFlow:
    return await ensure_default_content_flow(session, bot_id, owner_id, kind="welcome")


async def ensure_default_farewell_flow(session: AsyncSession, bot_id: int, owner_id: int) -> ContentFlow:
    return await ensure_default_content_flow(session, bot_id, owner_id, kind="farewell")


async def _clone_step(
    session: AsyncSession, source: ContentStep, target_version_id: int, position: int
) -> ContentStep:
    target = ContentStep(
        version_id=target_version_id,
        position=position,
        payload=source.payload,
        delay_after_seconds=source.delay_after_seconds,
    )
    session.add(target)
    await session.flush()
    attachments = list(
        (
            await session.scalars(
                select(ContentAttachment)
                .where(ContentAttachment.step_id == source.id)
                .order_by(ContentAttachment.position, ContentAttachment.id)
            )
        ).all()
    )
    session.add_all(
        [
            ContentAttachment(
                step_id=target.id,
                position=item.position,
                media_type=item.media_type,
                storage_key=item.storage_key,
                original_name=item.original_name,
                mime_type=item.mime_type,
                size=item.size,
                payload=item.payload,
            )
            for item in attachments
        ]
    )
    keyboard = await session.scalar(select(ContentKeyboard).where(ContentKeyboard.step_id == source.id))
    if keyboard is not None:
        target_keyboard = ContentKeyboard(
            step_id=target.id,
            kind=keyboard.kind,
            settings=keyboard.settings,
        )
        session.add(target_keyboard)
        await session.flush()
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
        session.add_all(
            [
                ContentKeyboardButton(
                    keyboard_id=target_keyboard.id,
                    row=button.row,
                    position=button.position,
                    text=button.text,
                    action_type=button.action_type,
                    value=button.value,
                    style=button.style,
                )
                for button in buttons
            ]
        )
    return target


async def open_draft(
    session: AsyncSession, owner_id: int, flow_id: int, author_telegram_id: int
) -> ContentFlowVersion:
    flow = await _owned_flow(session, owner_id, flow_id, lock=True)
    existing = await session.scalar(
        select(ContentFlowVersion).where(
            ContentFlowVersion.flow_id == flow.id,
            ContentFlowVersion.status == "draft",
        )
    )
    if existing is not None:
        return existing
    next_version = (
        int(
            await session.scalar(
                select(func.max(ContentFlowVersion.version)).where(ContentFlowVersion.flow_id == flow.id)
            )
            or 0
        )
        + 1
    )
    published = await session.scalar(
        select(ContentFlowVersion).where(
            ContentFlowVersion.flow_id == flow.id,
            ContentFlowVersion.status == "published",
        )
    )
    draft = ContentFlowVersion(
        flow_id=flow.id,
        version=next_version,
        status="draft",
        author_telegram_id=author_telegram_id,
        first_delay_seconds=published.first_delay_seconds if published else 0,
    )
    session.add(draft)
    await session.flush()
    if published is not None:
        steps = list(
            (
                await session.scalars(
                    select(ContentStep)
                    .where(ContentStep.version_id == published.id)
                    .order_by(ContentStep.position, ContentStep.id)
                )
            ).all()
        )
        for step in steps:
            await _clone_step(session, step, draft.id, step.position)
    await session.commit()
    return draft


async def draft_snapshot(session: AsyncSession, owner_id: int, version_id: int) -> DraftSnapshot:
    row = (
        await session.execute(
            select(ContentFlowVersion, ContentFlow)
            .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
            .join(ManagedBot, ManagedBot.id == ContentFlow.bot_id)
            .where(
                ContentFlowVersion.id == version_id,
                ContentFlowVersion.status == "draft",
                ManagedBot.owner_id == owner_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise ContentValidationError("Editable draft was not found")
    version, flow = row
    steps = list(
        (
            await session.scalars(
                select(ContentStep)
                .where(ContentStep.version_id == version.id)
                .order_by(ContentStep.position, ContentStep.id)
            )
        ).all()
    )
    return DraftSnapshot(flow, version, steps)


async def add_draft_step(
    session: AsyncSession,
    owner_id: int,
    version_id: int,
    payload: dict[str, Any],
    attachments: list[dict[str, Any]],
    *,
    delay_after_seconds: int = 1,
) -> ContentStep:
    version = await session.scalar(
        select(ContentFlowVersion)
        .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
        .join(ManagedBot, ManagedBot.id == ContentFlow.bot_id)
        .where(
            ContentFlowVersion.id == version_id,
            ContentFlowVersion.status == "draft",
            ManagedBot.owner_id == owner_id,
        )
        .with_for_update(of=ContentFlowVersion)
    )
    if version is None:
        raise ContentValidationError("Editable draft was not found")
    if not 0 <= delay_after_seconds <= 86_400:
        raise ContentValidationError("Step delay must be between 0 and 24 hours")
    max_position = await session.scalar(
        select(func.max(ContentStep.position)).where(ContentStep.version_id == version.id)
    )
    position = int(max_position) + 1 if max_position is not None else 0
    step = ContentStep(
        version_id=version.id,
        position=position,
        payload=payload,
        delay_after_seconds=delay_after_seconds,
    )
    session.add(step)
    await session.flush()
    session.add_all(
        [
            ContentAttachment(step_id=step.id, position=index, **attachment)
            for index, attachment in enumerate(attachments)
        ]
    )
    await session.commit()
    return step


async def set_step_delay(session: AsyncSession, owner_id: int, step_id: int, delay_seconds: int) -> None:
    if not 0 <= delay_seconds <= 86_400:
        raise ContentValidationError("Step delay must be between 0 and 24 hours")
    result = await session.execute(
        update(ContentStep)
        .where(
            ContentStep.id == step_id,
            ContentStep.version_id.in_(
                select(ContentFlowVersion.id)
                .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
                .join(ManagedBot, ManagedBot.id == ContentFlow.bot_id)
                .where(
                    ContentFlowVersion.status == "draft",
                    ManagedBot.owner_id == owner_id,
                )
            ),
        )
        .values(delay_after_seconds=delay_seconds)
    )
    if not cast(CursorResult[tuple[int]], result).rowcount:
        raise ContentValidationError("Editable step was not found")
    await session.commit()


async def move_step(session: AsyncSession, owner_id: int, step_id: int, direction: int) -> None:
    if direction not in {-1, 1}:
        raise ContentValidationError("Direction must be -1 or 1")
    step = await session.scalar(
        select(ContentStep)
        .join(ContentFlowVersion, ContentFlowVersion.id == ContentStep.version_id)
        .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
        .join(ManagedBot, ManagedBot.id == ContentFlow.bot_id)
        .where(
            ContentStep.id == step_id,
            ContentFlowVersion.status == "draft",
            ManagedBot.owner_id == owner_id,
        )
        .with_for_update(of=ContentFlowVersion)
    )
    if step is None:
        raise ContentValidationError("Editable step was not found")
    other = await session.scalar(
        select(ContentStep).where(
            ContentStep.version_id == step.version_id,
            ContentStep.position == step.position + direction,
        )
    )
    if other is None:
        return
    original = step.position
    step.position = 2_000_000_000
    await session.flush()
    other.position = original
    await session.flush()
    step.position = original + direction
    await session.commit()


async def copy_step(session: AsyncSession, owner_id: int, step_id: int) -> ContentStep:
    step = await session.scalar(
        select(ContentStep)
        .join(ContentFlowVersion, ContentFlowVersion.id == ContentStep.version_id)
        .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
        .join(ManagedBot, ManagedBot.id == ContentFlow.bot_id)
        .where(
            ContentStep.id == step_id,
            ContentFlowVersion.status == "draft",
            ManagedBot.owner_id == owner_id,
        )
        .with_for_update(of=ContentFlowVersion)
    )
    if step is None:
        raise ContentValidationError("Editable step was not found")
    await session.execute(
        update(ContentStep)
        .where(
            ContentStep.version_id == step.version_id,
            ContentStep.position > step.position,
        )
        .values(position=ContentStep.position + 100_000)
    )
    await session.execute(
        update(ContentStep)
        .where(
            ContentStep.version_id == step.version_id,
            ContentStep.position > 100_000,
        )
        .values(position=ContentStep.position - 99_999)
    )
    target = await _clone_step(session, step, step.version_id, step.position + 1)
    await session.commit()
    return target


async def set_first_delay(session: AsyncSession, owner_id: int, version_id: int, delay_seconds: int) -> None:
    if not 0 <= delay_seconds <= 86_400:
        raise ContentValidationError("Initial delay must be between 0 and 24 hours")
    result = await session.execute(
        update(ContentFlowVersion)
        .where(
            ContentFlowVersion.id == version_id,
            ContentFlowVersion.status == "draft",
            ContentFlowVersion.flow_id.in_(
                select(ContentFlow.id)
                .join(ManagedBot, ManagedBot.id == ContentFlow.bot_id)
                .where(ManagedBot.owner_id == owner_id)
            ),
        )
        .values(first_delay_seconds=delay_seconds)
    )
    if not cast(CursorResult[tuple[int]], result).rowcount:
        raise ContentValidationError("Editable draft was not found")
    await session.commit()


async def replace_step_keyboard(
    session: AsyncSession,
    owner_id: int,
    step_id: int,
    keyboard: dict[str, Any] | None,
) -> None:
    step = await session.scalar(
        select(ContentStep)
        .join(ContentFlowVersion, ContentFlowVersion.id == ContentStep.version_id)
        .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
        .join(ManagedBot, ManagedBot.id == ContentFlow.bot_id)
        .where(
            ContentStep.id == step_id,
            ContentFlowVersion.status == "draft",
            ManagedBot.owner_id == owner_id,
        )
        .with_for_update(of=ContentFlowVersion)
    )
    if step is None:
        raise ContentValidationError("Editable step was not found")
    normalized_keyboard = validate_step_keyboard(keyboard) if keyboard is not None else None
    existing = await session.scalar(select(ContentKeyboard).where(ContentKeyboard.step_id == step.id))
    if existing is not None:
        await session.delete(existing)
        await session.flush()
    if normalized_keyboard is None:
        await session.commit()
        return
    kind = normalized_keyboard["kind"]
    rows = normalized_keyboard["rows"]
    target = ContentKeyboard(
        step_id=step.id,
        kind=kind,
        settings=normalized_keyboard["settings"],
    )
    session.add(target)
    await session.flush()
    for row_index, row in enumerate(rows):
        if not isinstance(row, list) or not row:
            continue
        for position, raw in enumerate(row):
            if not isinstance(raw, dict):
                raise ContentValidationError("Keyboard button is invalid")
            text_value = raw["text"]
            action = raw["action_type"]
            value = raw["value"]
            style = raw["style"]
            session.add(
                ContentKeyboardButton(
                    keyboard_id=target.id,
                    row=row_index,
                    position=position,
                    text=text_value,
                    action_type=action,
                    value=value,
                    style=style,
                )
            )
    await session.commit()


async def delete_step(session: AsyncSession, owner_id: int, step_id: int) -> list[str]:
    step = await session.scalar(
        select(ContentStep)
        .join(ContentFlowVersion, ContentFlowVersion.id == ContentStep.version_id)
        .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
        .join(ManagedBot, ManagedBot.id == ContentFlow.bot_id)
        .where(
            ContentStep.id == step_id,
            ContentFlowVersion.status == "draft",
            ManagedBot.owner_id == owner_id,
        )
        .with_for_update(of=ContentFlowVersion)
    )
    if step is None:
        raise ContentValidationError("Editable step was not found")
    version_id, position = step.version_id, step.position
    candidate_keys = list(
        await session.scalars(
            select(ContentAttachment.storage_key).where(ContentAttachment.step_id == step.id)
        )
    )
    await session.delete(step)
    await session.flush()
    await session.execute(
        update(ContentStep)
        .where(ContentStep.version_id == version_id, ContentStep.position > position)
        .values(position=ContentStep.position + 100_000)
    )
    await session.execute(
        update(ContentStep)
        .where(ContentStep.version_id == version_id, ContentStep.position > 100_000)
        .values(position=ContentStep.position - 100_001)
    )
    await session.commit()
    if not candidate_keys:
        return []
    referenced = set(
        await session.scalars(
            select(ContentAttachment.storage_key).where(ContentAttachment.storage_key.in_(candidate_keys))
        )
    )
    return sorted(set(candidate_keys) - referenced)


async def publish_draft(session: AsyncSession, owner_id: int, version_id: int) -> ContentFlowVersion:
    version = await session.scalar(
        select(ContentFlowVersion)
        .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
        .join(ManagedBot, ManagedBot.id == ContentFlow.bot_id)
        .where(
            ContentFlowVersion.id == version_id,
            ContentFlowVersion.status == "draft",
            ManagedBot.owner_id == owner_id,
        )
        .with_for_update(of=ContentFlowVersion)
    )
    if version is None:
        raise ContentValidationError("Editable draft was not found")
    steps = list(
        (
            await session.scalars(
                select(ContentStep)
                .where(ContentStep.version_id == version.id)
                .order_by(ContentStep.position, ContentStep.id)
            )
        ).all()
    )
    if not steps:
        raise ContentValidationError("Add at least one step before publishing")
    flow = await session.get(ContentFlow, version.flow_id)
    if flow is None:
        raise RuntimeError("Content flow disappeared")
    if flow.kind == "farewell":
        attachment_count = int(
            await session.scalar(
                select(func.count(ContentAttachment.id)).where(
                    ContentAttachment.step_id.in_([step.id for step in steps])
                )
            )
            or 0
        )
        if len(steps) > 5:
            raise ContentValidationError("Farewell chain supports up to 5 messages")
        if attachment_count > 5:
            raise ContentValidationError("Farewell chain supports up to 5 files")
    elif flow_timeline_seconds(version, steps) > JOIN_REQUEST_MAX_TIMELINE_SECONDS:
        raise ContentValidationError(
            "Приветственная цепочка длится больше 4 минут. Сократите задержку до первого "
            "сообщения или паузы между шагами — иначе Telegram закроет окно заявки."
        )
    for step in steps[:-1]:
        if not 1 <= step.delay_after_seconds <= 86_400:
            raise ContentValidationError("Every non-final step needs a 1s–24h delay")
    steps[-1].delay_after_seconds = 0
    await session.execute(
        update(ContentFlowVersion)
        .where(
            ContentFlowVersion.flow_id == version.flow_id,
            ContentFlowVersion.status == "published",
        )
        .values(status="archived")
    )
    version.status = "published"
    version.published_at = datetime.now(UTC)
    session.add(
        EventLog(
            bot_id=flow.bot_id,
            owner_id=owner_id,
            event_type="content_flow_published",
            message=f"{flow.kind.capitalize()} content flow published",
            context={"flow_id": flow.id, "version": version.version, "steps": len(steps)},
        )
    )
    await session.commit()
    return version


async def set_flow_assignments(
    session: AsyncSession,
    owner_id: int,
    flow_id: int,
    channel_ids: list[int] | None,
) -> None:
    flow = await _owned_flow(session, owner_id, flow_id, lock=True)
    await session.execute(delete(FlowChannelAssignment).where(FlowChannelAssignment.flow_id == flow.id))
    if channel_ids is None:
        flow.assignment_mode = "all"
    else:
        unique_ids = sorted(set(channel_ids))
        valid_ids = set(
            await session.scalars(
                select(Channel.id).where(
                    Channel.bot_id == flow.bot_id,
                    Channel.id.in_(unique_ids),
                    Channel.is_active.is_(True),
                )
            )
        )
        if valid_ids != set(unique_ids):
            raise ContentValidationError("One or more channels do not belong to this bot")
        flow.assignment_mode = "selected"
        session.add_all([FlowChannelAssignment(flow_id=flow.id, channel_id=item) for item in unique_ids])
    await session.commit()
