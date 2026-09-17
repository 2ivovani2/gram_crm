"""Curated templates copied into isolated, unassigned drafts. No Telegram writes."""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .commercial import access_for_owner
from .content_compiler import AttachmentSpec, compile_step
from .content_service import ContentValidationError, validate_step_keyboard
from .db import session_factory
from .delays import MAX_DELAY_SECONDS
from .idempotency import claim_request, store_response
from .models import (
    ContentAttachment,
    ContentFlow,
    ContentFlowVersion,
    ContentKeyboard,
    ContentKeyboardButton,
    ContentStep,
    ManagedBot,
    MessageTemplate,
    Owner,
)
from .rich_text import validate_rich_text


class CatalogAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_type: str
    storage_key: str = Field(pattern=r"^catalog/[^.].*", max_length=500)
    original_name: str = ""
    mime_type: str = ""
    size: int = Field(default=0, ge=0, le=20 * 1024 * 1024)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("media_type")
    @classmethod
    def supported_media(cls, value: str) -> str:
        if value not in {"photo", "video", "animation", "document", "audio", "voice", "video_note", "sticker"}:
            raise ValueError("Unsupported template media")
        return value


class CatalogStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any]
    attachments: list[CatalogAttachment] = Field(default_factory=list, max_length=5)
    keyboard: dict[str, Any] | None = None
    delay_after_seconds: int = Field(default=0, ge=0, le=MAX_DELAY_SECONDS)

    @field_validator("keyboard")
    @classmethod
    def keyboard_valid(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if not value:
            return None
        normalized = validate_step_keyboard(value)
        if normalized["kind"] != "inline" or any(b["action_type"] != "url" for row in normalized["rows"] for b in row):
            raise ValueError("Catalog supports portable URL buttons only")
        return normalized

    @model_validator(mode="after")
    def compilable(self) -> CatalogStep:
        validate_rich_text(self.payload)
        for media in self.attachments:
            validate_rich_text(media.payload)
        if not self.attachments and not (self.payload.get("text") or self.payload.get("caption")):
            raise ValueError("Template step needs content")
        for operation in compile_step(self.payload, [AttachmentSpec(**item.model_dump()) for item in self.attachments], self.keyboard):
            validate_rich_text(operation.payload)
            for item in operation.media:
                validate_rich_text(item.get("payload") or {})
        return self


class CatalogSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_delay_seconds: int = Field(default=0, ge=0, le=MAX_DELAY_SECONDS)
    steps: list[CatalogStep] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def file_limit(self) -> CatalogSpec:
        if sum(len(step.attachments) for step in self.steps) > 5:
            raise ValueError("Maximum five files per template")
        return self


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=r"^[a-z0-9-]{1,80}$")
    revision: int = Field(ge=1)
    kind: Literal["welcome", "farewell"]
    title: str = Field(min_length=1, max_length=128)
    author: str = Field(default="Gramly", min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    screenshots: list[str] = Field(default_factory=list, max_length=5)
    conversion: str | None = Field(default=None, max_length=80)
    requires_business: bool = False
    spec: CatalogSpec

    @field_validator("tags")
    @classmethod
    def normalized_tags(cls, values: list[str]) -> list[str]:
        tags = [value.strip() for value in values]
        if any(not tag or len(tag) > 80 or any(ord(c) < 32 for c in tag) for tag in tags):
            raise ValueError("Tags must contain 1–80 printable characters")
        return list(dict.fromkeys(tags))

    @field_validator("screenshots")
    @classmethod
    def local_screenshots(cls, values: list[str]) -> list[str]:
        if any(not value.startswith("/app/catalog/") or ".." in value for value in values):
            raise ValueError("Screenshots must be bundled under /app/catalog/")
        return values


def template_view(row: MessageTemplate) -> dict[str, Any]:
    spec = CatalogSpec.model_validate(row.spec)
    return {
        "id": row.id, "slug": row.slug, "revision": row.revision, "kind": row.kind, "title": row.title,
        "author": row.author, "description": row.description, "tags": row.tags, "screenshots": row.screenshots,
        "contains_premium_emoji": '"custom_emoji"' in json.dumps(row.spec),
        "conversion": row.conversion, "requires_business": row.requires_business, "step_count": len(spec.steps),
        "first_delay_seconds": spec.first_delay_seconds,
        "steps": [{"text": s.payload.get("text") or s.payload.get("caption") or "Медиа-сообщение",
                   "delay_after_seconds": s.delay_after_seconds,
                   "media_types": [a.media_type for a in s.attachments]} for s in spec.steps],
    }


async def install_template(session: AsyncSession, *, owner_id: int, bot_id: int, template_id: int, key: str) -> dict[str, Any]:
    bot = await session.scalar(select(ManagedBot).where(ManagedBot.id == bot_id,
        ManagedBot.owner_id == owner_id, ManagedBot.is_active.is_(True)).with_for_update())
    if bot is None:
        raise ContentValidationError("Бот недоступен")
    stored = await claim_request(session, owner_id=owner_id, key=key,
                                 payload={"action": "catalog.install", "bot_id": bot_id, "template_id": template_id})
    if stored:
        return stored.body
    template = await session.scalar(select(MessageTemplate).where(MessageTemplate.id == template_id,
                                                                  MessageTemplate.is_active.is_(True)))
    if template is None:
        raise ContentValidationError("Шаблон больше недоступен")
    access = await access_for_owner(session, owner_id)
    if template.requires_business and access.plan_slug != "business":
        raise ContentValidationError("Для этого шаблона необходим Business")
    spec = CatalogSpec.model_validate(template.spec)
    flow = ContentFlow(bot_id=bot_id, kind=template.kind, name=f"{template.title[:110]} · {uuid.uuid4().hex[:8]}",
                       assignment_mode="selected", is_active=True)
    session.add(flow)
    await session.flush()
    owner = await session.get(Owner, owner_id)
    assert owner is not None
    version = ContentFlowVersion(flow_id=flow.id, version=1, status="draft", author_telegram_id=owner.telegram_id,
                                 first_delay_seconds=spec.first_delay_seconds)
    session.add(version)
    await session.flush()
    for position, item in enumerate(spec.steps):
        step = ContentStep(version_id=version.id, position=position, payload=deepcopy(item.payload),
                           delay_after_seconds=item.delay_after_seconds)
        session.add(step)
        await session.flush()
        for index, attachment in enumerate(item.attachments):
            session.add(ContentAttachment(step_id=step.id, position=index, **attachment.model_dump()))
        if item.keyboard:
            keyboard = ContentKeyboard(step_id=step.id, kind=item.keyboard["kind"])
            session.add(keyboard)
            await session.flush()
            for row_index, buttons in enumerate(item.keyboard["rows"]):
                for column, button in enumerate(buttons):
                    session.add(ContentKeyboardButton(keyboard_id=keyboard.id, row=row_index, position=column, **button))
    body = {"flow_id": flow.id, "version_id": version.id, "name": flow.name, "kind": flow.kind,
            "assignment_mode": "selected", "status": "draft"}
    # One commit includes the new flow, steps and idempotency response.
    await store_response(session, owner_id=owner_id, key=key, status=201, body=body)
    return body


async def load_catalog(entries: list[CatalogEntry], *, apply: bool) -> None:
    if len({e.slug for e in entries}) != len(entries):
        raise ValueError("Load exactly one revision per template slug")
    async with session_factory() as session:
        if apply:
            await session.execute(text("SELECT pg_advisory_xact_lock(7162814301)"))
        report = []
        for entry in entries:
            existing = await session.scalar(select(MessageTemplate).where(MessageTemplate.slug == entry.slug,
                                                                           MessageTemplate.revision == entry.revision))
            data = entry.model_dump(mode="json")
            if existing:
                actual = {key: getattr(existing, key) for key in data}
                if actual != data:
                    raise ValueError(f"Immutable revision changed: {entry.slug}:{entry.revision}")
            else:
                latest = await session.scalar(select(MessageTemplate.revision).where(MessageTemplate.slug == entry.slug)
                                              .order_by(MessageTemplate.revision.desc()).limit(1))
                if latest is not None and entry.revision <= latest:
                    raise ValueError(f"Revision must increase: {entry.slug}")
                if apply:
                    await session.execute(update(MessageTemplate).where(MessageTemplate.slug == entry.slug).values(is_active=False))
                    await session.execute(insert(MessageTemplate).values(**data, is_active=True))
            report.append({"slug": entry.slug, "revision": entry.revision, "action": "unchanged" if existing else "add", "apply": apply})
        if apply:
            await session.commit()
        for item in report:
            print(json.dumps(item))


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from .catalog_starters import starter_entries
    raw = json.loads(args.file.read_text()) if args.file else starter_entries()
    entries = [CatalogEntry.model_validate(item) for item in raw]
    asyncio.run(load_catalog(entries, apply=args.apply))
