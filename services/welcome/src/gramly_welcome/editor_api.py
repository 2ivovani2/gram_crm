"""Owner-scoped, atomic editor operations. Legacy endpoints remain compatible."""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Annotated, Any, cast

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .app_api import CsrfUserDep, CurrentUserDep, SessionDep, flow_draft
from .content_compiler import AttachmentSpec, compile_step
from .content_service import publish_draft, set_flow_assignments, validate_step_keyboard
from .db import owner_transaction
from .delays import MAX_DELAY_SECONDS
from .idempotency import IdempotencyConflictError, claim_request, store_response
from .models import (
    Channel,
    ContentAttachment,
    ContentFlow,
    ContentFlowVersion,
    ContentKeyboard,
    ContentKeyboardButton,
    ContentStep,
    EditorMedia,
    FlowChannelAssignment,
    ManagedBot,
)
from .rich_text import validate_rich_text

router = APIRouter(prefix="/api/v1/editor", tags=["chain-editor"])


class EditorStep(BaseModel):
    id: int | None = None
    payload: dict[str, Any]
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=5)
    keyboard: dict[str, Any] | None = None
    delay_after_seconds: int = Field(default=0, ge=0, le=MAX_DELAY_SECONDS)


class SaveDraft(BaseModel):
    expected_revision: int = Field(ge=1)
    first_delay_seconds: int = Field(ge=0, le=MAX_DELAY_SECONDS)
    button_style: str | None = None
    steps: list[EditorStep] = Field(min_length=1, max_length=5)


class LaunchDraft(BaseModel):
    expected_revision: int = Field(ge=1)
    channel_ids: list[int] = Field(min_length=1, max_length=500)


def legacy_step(payload: dict[str, Any], media: list[dict[str, Any]]) -> bool:
    """Content outside the single-caption composer remains byte-for-byte intact."""
    return bool(
        "sequence" in payload
        or "items" in payload
        or (not media and payload.get("type", "text") != "text")
        or any(m["media_type"] in {"sticker", "video_note"} for m in media)
        or any((m.get("payload") or {}).get("caption") for m in media[1:])
    )


async def owned_version(session: Any, owner_id: int, version_id: int, expected: int) -> ContentFlowVersion:
    await session.scalar(
        select(ContentFlow)
        .join(ContentFlowVersion)
        .join(ManagedBot, ManagedBot.id == ContentFlow.bot_id)
        .where(ContentFlowVersion.id == version_id, ManagedBot.owner_id == owner_id)
        .with_for_update(of=ContentFlow)
    )
    version = await session.scalar(
        select(ContentFlowVersion)
        .join(ContentFlow)
        .join(ManagedBot)
        .where(
            ContentFlowVersion.id == version_id,
            ManagedBot.owner_id == owner_id,
            ManagedBot.is_active.is_(True),
        )
        .with_for_update(of=ContentFlowVersion)
        .execution_options(populate_existing=True)
    )
    if version is None:
        raise HTTPException(404, "Черновик недоступен")
    if version.status != "draft" or version.edit_revision != expected:
        raise HTTPException(
            409,
            "Цепочка изменена в другом окне или в боте. Ваши правки сохранены на экране; загрузите актуальную версию.",
        )
    return cast(ContentFlowVersion, version)


async def editor_document(flow_id: int, user: Any, session: Any) -> dict[str, Any]:
    # Serialize all draft writers while assembling the document.
    await session.scalar(
        select(ContentFlow)
        .join(ManagedBot)
        .where(ContentFlow.id == flow_id, ManagedBot.owner_id == user.auth.owner.id)
        .with_for_update(of=ContentFlow)
    )
    await session.scalars(
        select(ContentFlowVersion)
        .where(ContentFlowVersion.flow_id == flow_id, ContentFlowVersion.status == "draft")
        .with_for_update()
    )
    document: Any = await flow_draft(flow_id, user, session)
    version_id = document["version"]["id"]
    version = await session.get(ContentFlowVersion, version_id, populate_existing=True)
    document["version"]["edit_revision"] = version.edit_revision
    flow = await session.get(ContentFlow, flow_id)
    bot = await session.get(ManagedBot, flow.bot_id)
    channels = list(
        await session.scalars(
            select(Channel).where(Channel.bot_id == flow.bot_id, Channel.is_active.is_(True))
        )
    )
    selected = list(
        await session.scalars(
            select(FlowChannelAssignment.channel_id).where(FlowChannelAssignment.flow_id == flow_id)
        )
    )
    document["flow"].update(
        bot_id=flow.bot_id,
        bot_username=bot.username,
        channel_ids=[c.id for c in channels] if flow.assignment_mode == "all" else selected,
    )
    document["channels"] = [
        {"id": c.id, "title": c.title, "can_invite_users": c.can_invite_users} for c in channels
    ]
    for step in document["steps"]:
        keyboard = await session.scalar(select(ContentKeyboard).where(ContentKeyboard.step_id == step["id"]))
        step["keyboard"] = None
        if keyboard:
            buttons = list(
                await session.scalars(
                    select(ContentKeyboardButton)
                    .where(ContentKeyboardButton.keyboard_id == keyboard.id)
                    .order_by(ContentKeyboardButton.row, ContentKeyboardButton.position)
                )
            )
            rows: dict[int, list[dict[str, Any]]] = {}
            for b in buttons:
                rows.setdefault(b.row, []).append(
                    {"text": b.text, "action_type": b.action_type, "value": b.value, "style": b.style}
                )
            step["keyboard"] = {
                "kind": keyboard.kind,
                "settings": keyboard.settings,
                "rows": list(rows.values()),
            }
        attachments = list(
            await session.scalars(
                select(ContentAttachment)
                .where(ContentAttachment.step_id == step["id"])
                .order_by(ContentAttachment.position)
            )
        )
        step["attachments"] = [
            {
                "id": a.id,
                **{
                    k: getattr(a, k)
                    for k in ("media_type", "storage_key", "original_name", "mime_type", "size", "payload")
                },
                "preview_url": f"/api/v1/editor/attachments/{a.id}",
            }
            for a in attachments
        ]
    for step in document["steps"]:
        step["editor_read_only"] = legacy_step(step["payload"], step["attachments"])
    return cast(dict[str, Any], document)


@router.get("/flows/{flow_id}")
async def read_editor(flow_id: int, user: CurrentUserDep, session: SessionDep) -> dict[str, Any]:
    token = owner_transaction.set(session)
    try:
        result = await editor_document(flow_id, user, session)
    finally:
        owner_transaction.reset(token)
    await session.commit()
    return result


@router.put("/drafts/{version_id}")
async def save_editor(
    version_id: int,
    payload: SaveDraft,
    user: CsrfUserDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    owner_id = user.auth.owner.id
    try:
        stored = await claim_request(
            session,
            owner_id=owner_id,
            key=idempotency_key,
            payload={"action": "editor.save", "version_id": version_id, **payload.model_dump()},
        )
        if stored:
            return stored.body
        version = await owned_version(session, owner_id, version_id, payload.expected_revision)
        if payload.button_style not in {None, "default", "primary", "success", "danger"}:
            raise ValueError("Неизвестный цвет кнопок")
        if sum(len(s.attachments) for s in payload.steps) > 5:
            raise ValueError("В цепочке допускается до 5 файлов")
        existing_steps = {
            s.id: s
            for s in await session.scalars(select(ContentStep).where(ContentStep.version_id == version_id))
        }
        existing_media = {
            a.id: a
            for a in await session.scalars(
                select(ContentAttachment).join(ContentStep).where(ContentStep.version_id == version_id)
            )
        }
        normalized = []
        for item in payload.steps:
            if item.id is not None and item.id not in existing_steps:
                raise ValueError("Сообщение не принадлежит черновику")
            media = []
            for raw in item.attachments:
                if (raw.get("id") is not None and not isinstance(raw["id"], int)) or not isinstance(
                    raw.get("payload", {}), dict
                ):
                    raise ValueError("Некорректный файл")
                if raw.get("id") in existing_media:
                    a = existing_media[raw["id"]]
                    media.append(
                        {
                            k: deepcopy(getattr(a, k))
                            for k in (
                                "media_type",
                                "storage_key",
                                "original_name",
                                "mime_type",
                                "size",
                                "payload",
                            )
                        }
                    )
                elif raw.get("media_id"):
                    asset = await session.get(EditorMedia, uuid.UUID(str(raw["media_id"])))
                    if asset is None or asset.owner_id != owner_id:
                        raise ValueError("Файл недоступен")
                    media.append(deepcopy(asset.descriptor))
                else:
                    raise ValueError("Загрузите файл через редактор")
            content = deepcopy(item.payload)
            original = existing_steps.get(item.id or 0)
            original_media = [
                a
                for a in sorted(existing_media.values(), key=lambda a: a.position)
                if original and a.step_id == original.id
            ]
            if legacy_step(content, media) or (
                original
                and legacy_step(
                    original.payload,
                    [{"media_type": a.media_type, "payload": a.payload} for a in original_media],
                )
            ):
                original = existing_steps.get(item.id or 0)
                if original is None or content != original.payload:
                    raise ValueError(
                        "Составное сообщение можно изменить в боте; в Mini App оно сохраняется без изменений"
                    )
                original_ids = [
                    a.id
                    for a in sorted(existing_media.values(), key=lambda a: a.position)
                    if a.step_id == original.id
                ]
                if [m.get("id") for m in item.attachments] != original_ids:
                    raise ValueError("Вложения составного сообщения изменяются в боте")
            else:
                validate_rich_text(content)
                # The primary caption is edited with the message text. Never accept
                # storage metadata or arbitrary media payload from the browser.
                if media:
                    first = media[0]
                    rich = content.get("caption", content.get("text", ""))
                    entities = content.get("caption_entities", content.get("entities", []))
                    if item.attachments[0].get("payload", {}).get("caption") is not None:
                        rich = item.attachments[0]["payload"]["caption"]
                        entities = item.attachments[0]["payload"].get("caption_entities", [])
                    first["payload"] = {
                        **(first.get("payload") or {}),
                        "caption": rich,
                        "caption_entities": entities,
                    }
                    validate_rich_text(first["payload"])
                if not media and not (content.get("text") or content.get("caption")):
                    raise ValueError("Добавьте текст или файл")
            keyboard = validate_step_keyboard(item.keyboard) if item.keyboard else None
            compile_step(content, [AttachmentSpec(**m) for m in media], keyboard)
            normalized.append((content, media, keyboard, item.delay_after_seconds))
        await session.execute(delete(ContentStep).where(ContentStep.version_id == version_id))
        for position, (content, media, keyboard, delay) in enumerate(normalized):
            step = ContentStep(
                version_id=version_id,
                position=position,
                payload=content,
                delay_after_seconds=delay if position < len(normalized) - 1 else 0,
            )
            session.add(step)
            await session.flush()
            for index, m in enumerate(media):
                session.add(ContentAttachment(step_id=step.id, position=index, **m))
            if keyboard:
                kb = ContentKeyboard(
                    step_id=step.id, kind=keyboard["kind"], settings=keyboard.get("settings", {})
                )
                session.add(kb)
                await session.flush()
                for row, buttons in enumerate(keyboard["rows"]):
                    for column, b in enumerate(buttons):
                        session.add(ContentKeyboardButton(keyboard_id=kb.id, row=row, position=column, **b))
        version.first_delay_seconds = payload.first_delay_seconds
        version.button_style = payload.button_style
        await session.flush()
        await session.refresh(version)
        # open_draft's existing commit becomes a flush inside this unit of work.
        token = owner_transaction.set(session)
        try:
            body = await editor_document(version.flow_id, user, session)
        finally:
            owner_transaction.reset(token)
        await store_response(session, owner_id=owner_id, key=idempotency_key, status=200, body=body)
        return body
    except HTTPException:
        await session.rollback()
        raise
    except IdempotencyConflictError as exc:
        await session.rollback()
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post("/drafts/{version_id}/launch")
async def launch_editor(
    version_id: int,
    payload: LaunchDraft,
    user: CsrfUserDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    owner_id = user.auth.owner.id
    try:
        stored = await claim_request(
            session,
            owner_id=owner_id,
            key=idempotency_key,
            payload={"action": "editor.launch", "version_id": version_id, **payload.model_dump()},
        )
        if stored:
            return stored.body
        version = await owned_version(session, owner_id, version_id, payload.expected_revision)
        steps = list(await session.scalars(select(ContentStep).where(ContentStep.version_id == version_id)))
        for step in steps:
            attachments = list(
                await session.scalars(
                    select(ContentAttachment)
                    .where(ContentAttachment.step_id == step.id)
                    .order_by(ContentAttachment.position)
                )
            )
            specs = [
                AttachmentSpec(
                    **{
                        k: getattr(a, k)
                        for k in (
                            "media_type",
                            "storage_key",
                            "original_name",
                            "mime_type",
                            "size",
                            "payload",
                        )
                    }
                )
                for a in attachments
            ]
            for operation in compile_step(step.payload, specs):
                validate_rich_text(operation.payload)
                if operation.operation_type == "text" and not str(operation.payload.get("text", "")).strip():
                    raise ValueError("Добавьте текст или удалите пустое сообщение")
                for media in operation.media:
                    validate_rich_text(media.get("payload") or {})
        token = owner_transaction.set(session)
        try:
            flow = await session.get(ContentFlow, version.flow_id)
            if flow is None:
                raise ValueError("Цепочка недоступна")
            flow.is_active = True
            await set_flow_assignments(session, owner_id, version.flow_id, payload.channel_ids)
            await publish_draft(session, owner_id, version_id)
        finally:
            owner_transaction.reset(token)
        body = {
            "flow_id": version.flow_id,
            "version_id": version_id,
            "status": "published",
            "channel_ids": sorted(set(payload.channel_ids)),
        }
        await store_response(session, owner_id=owner_id, key=idempotency_key, status=200, body=body)
        return body
    except HTTPException:
        await session.rollback()
        raise
    except IdempotencyConflictError as exc:
        await session.rollback()
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc
