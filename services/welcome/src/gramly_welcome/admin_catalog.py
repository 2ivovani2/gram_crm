"""Versioned catalog administration. Customer copies are never mutated."""
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select, text, update

from .admin_api import AdminDep, MutationAdminDep, SessionDep, _audit
from .message_catalog import CatalogEntry
from .models import MessageTemplate

router = APIRouter(prefix="/api/admin/v1/catalog", tags=["welcome-admin-catalog"])


def admin_template(row: MessageTemplate) -> dict[str, Any]:
    return {"id": row.id, "is_active": row.is_active,
            **{name: getattr(row, name) for name in CatalogEntry.model_fields}}


class CatalogSave(BaseModel):
    entry: CatalogEntry
    expected_revision: int = Field(ge=0)

    @field_validator("entry")
    @classmethod
    def nonempty_labels(cls, entry: CatalogEntry) -> CatalogEntry:
        for field in ("title", "author", "description"):
            if not getattr(entry, field).strip():
                raise ValueError("Название, автор и описание не могут быть пустыми")
        return entry


@router.get("")
async def list_templates(_admin: AdminDep, session: SessionDep, q: str = Query("", max_length=128),
                         kind: str = "", state: str = "all", page: int = Query(1, ge=1, le=10000)) -> dict[str, Any]:
    if kind not in {"", "welcome", "farewell"} or state not in {"all", "published", "hidden"}:
        raise HTTPException(422, "Неизвестный фильтр")
    query = select(MessageTemplate)
    if q.strip():
        query = query.where(or_(MessageTemplate.title.icontains(q.strip(), autoescape=True),
                               MessageTemplate.slug.icontains(q.strip(), autoescape=True)))
    if kind:
        query = query.where(MessageTemplate.kind == kind)
    if state != "all":
        query = query.where(MessageTemplate.is_active.is_(state == "published"))
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(query.order_by(MessageTemplate.created_at.desc(), MessageTemplate.id.desc())
                                .offset((page - 1) * 20).limit(20))
    return {"items": [admin_template(row) for row in rows], "total": total or 0, "page": page}


@router.post("")
async def save_template(payload: CatalogSave, admin: MutationAdminDep, session: SessionDep) -> dict[str, Any]:
    # Share the loader's lock, including the first insert where no row exists yet.
    await session.execute(text("SELECT pg_advisory_xact_lock(7162814301)"))
    entry = payload.entry
    latest = await session.scalar(select(MessageTemplate).where(MessageTemplate.slug == entry.slug)
                                  .order_by(MessageTemplate.revision.desc()).limit(1))
    data = entry.model_dump(mode="json")
    if latest and all(getattr(latest, key) == value for key, value in data.items()):
        return admin_template(latest)  # Retry of the exact same immutable save.
    revision = latest.revision if latest else 0
    if payload.expected_revision != revision or entry.revision != revision + 1:
        raise HTTPException(409, "Шаблон уже изменён. Обновите список перед сохранением.")
    if latest and latest.kind != entry.kind:
        raise HTTPException(422, "Тип существующего шаблона менять нельзя. Создайте новый шаблон.")
    row = MessageTemplate(**data, is_active=False)
    session.add(row)
    await session.flush()
    await _audit(session, admin, "catalog_revision_created", {"template_id": row.id, "revision": row.revision})
    await session.commit()
    return admin_template(row)


@router.post("/{template_id}/publish")
async def publish_template(template_id: int, admin: MutationAdminDep, session: SessionDep) -> dict[str, Any]:
    await session.execute(text("SELECT pg_advisory_xact_lock(7162814301)"))
    row = await session.get(MessageTemplate, template_id)
    if row is None:
        raise HTTPException(404, "Шаблон не найден")
    latest = await session.scalar(select(func.max(MessageTemplate.revision)).where(MessageTemplate.slug == row.slug))
    if row.revision != latest:
        raise HTTPException(409, "Есть более новая версия. Опубликуйте её или создайте новую копию старой.")
    CatalogEntry.model_validate({name: getattr(row, name) for name in CatalogEntry.model_fields})
    if not row.is_active:
        await session.execute(update(MessageTemplate).where(MessageTemplate.slug == row.slug).values(is_active=False))
        row.is_active = True
        await _audit(session, admin, "catalog_published", {"template_id": row.id, "revision": row.revision})
        await session.commit()
    return admin_template(row)


@router.delete("/{template_id}")
async def archive_template(template_id: int, admin: MutationAdminDep, session: SessionDep) -> dict[str, Any]:
    await session.execute(text("SELECT pg_advisory_xact_lock(7162814301)"))
    row = await session.get(MessageTemplate, template_id)
    if row is None:
        raise HTTPException(404, "Шаблон не найден")
    if row.is_active:
        row.is_active = False
        await _audit(session, admin, "catalog_archived", {"template_id": row.id, "revision": row.revision})
        await session.commit()
    return admin_template(row)
