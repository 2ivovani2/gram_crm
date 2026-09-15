import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome.admin_api import AdminIdentity
from gramly_welcome.admin_catalog import CatalogSave, archive_template, publish_template, save_template
from gramly_welcome.catalog_starters import starter_entries
from gramly_welcome.main import app
from gramly_welcome.message_catalog import CatalogEntry
from gramly_welcome.models import EventLog, MessageTemplate


def test_catalog_admin_requires_sso_and_mutation_header() -> None:
    with TestClient(app) as client:
        assert client.get("/api/admin/v1/catalog").status_code == 403
        assert client.post("/api/admin/v1/catalog/1/publish", headers={
            "X-authentik-username": "test", "X-authentik-groups": "gramly-owners",
        }).status_code == 403
        assert client.delete("/api/admin/v1/catalog/1", headers={
            "X-authentik-username": "test", "X-authentik-groups": "employees", "X-Gramly-Admin-Request": "1",
        }).status_code == 403


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WELCOME_TEST_DATABASE_URL"), reason="PostgreSQL required")
async def test_catalog_admin_immutable_versions_replay_publish_and_archive() -> None:
    engine = create_async_engine(os.environ["WELCOME_TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    raw = starter_entries()[0]
    raw["slug"] = "admin-test-" + uuid.uuid4().hex
    entry = CatalogEntry.model_validate(raw)
    admin = AdminIdentity(username="catalog-test", groups={"gramly-owners"})
    ids = []
    try:
        async with sessions() as session:
            first = await save_template(CatalogSave(entry=entry, expected_revision=0), admin, session)
            ids.append(first["id"])
            assert first["is_active"] is False
            replay = await save_template(CatalogSave(entry=entry, expected_revision=0), admin, session)
            assert replay["id"] == first["id"]
            assert (await publish_template(first["id"], admin, session))["is_active"] is True
            second_entry = entry.model_copy(update={"revision": 2, "title": "New version"})
            second = await save_template(CatalogSave(entry=second_entry, expected_revision=1), admin, session)
            ids.append(second["id"])
            assert second["is_active"] is False
            old = await session.get(MessageTemplate, first["id"])
            assert old is not None and old.is_active and old.title == entry.title
            with pytest.raises(HTTPException) as stale:
                await save_template(CatalogSave(entry=entry.model_copy(update={"revision":2,"title":"Stale"}), expected_revision=1), admin, session)
            assert stale.value.status_code == 409
            await session.rollback()
            await publish_template(second["id"], admin, session)
            old = await session.get(MessageTemplate, first["id"])
            assert old is not None and not old.is_active and old.spec == entry.spec.model_dump(mode="json")
            with pytest.raises(HTTPException) as outdated:
                await publish_template(first["id"], admin, session)
            assert outdated.value.status_code == 409
            await session.rollback()
            assert (await archive_template(second["id"], admin, session))["is_active"] is False
            assert (await archive_template(second["id"], admin, session))["is_active"] is False
            rows = list(await session.scalars(select(MessageTemplate).where(MessageTemplate.slug == entry.slug)))
            assert len(rows) == 2  # Archive does not destroy the immutable history.
    finally:
        async with sessions() as session:
            await session.execute(delete(EventLog).where(EventLog.context["template_id"].as_integer().in_(ids)))
            await session.execute(delete(MessageTemplate).where(MessageTemplate.slug == entry.slug))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WELCOME_TEST_DATABASE_URL"), reason="PostgreSQL required")
async def test_concurrent_catalog_save_creates_one_version() -> None:
    engine = create_async_engine(os.environ["WELCOME_TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    raw = starter_entries()[0]
    raw["slug"] = "admin-race-" + uuid.uuid4().hex
    entry = CatalogEntry.model_validate(raw)
    admin = AdminIdentity(username="catalog-test", groups={"gramly-owners"})

    async def save() -> dict:
        async with sessions() as session:
            return await save_template(CatalogSave(entry=entry, expected_revision=0), admin, session)

    try:
        results = await asyncio.gather(save(), save())
        assert results[0]["id"] == results[1]["id"]
        async with sessions() as session:
            rows = list(await session.scalars(select(MessageTemplate).where(MessageTemplate.slug == entry.slug)))
            assert len(rows) == 1
            assert not rows[0].is_active
    finally:
        async with sessions() as session:
            ids = select(MessageTemplate.id).where(MessageTemplate.slug == entry.slug)
            await session.execute(delete(EventLog).where(EventLog.context["template_id"].as_integer().in_(ids)))
            await session.execute(delete(MessageTemplate).where(MessageTemplate.slug == entry.slug))
            await session.commit()
        await engine.dispose()
