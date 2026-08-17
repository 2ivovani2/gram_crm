from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from gramly_welcome import api
from gramly_welcome.config import Settings, get_settings
from gramly_welcome.db import session_dependency
from gramly_welcome.main import app
from gramly_welcome.repository import BotWebhookIdentity


class FakeSession:
    async def rollback(self) -> None:
        return None

    async def commit(self) -> None:
        return None


@pytest.fixture
def client() -> Iterator[TestClient]:
    async def session_override() -> AsyncIterator[FakeSession]:
        yield FakeSession()

    app.dependency_overrides[session_dependency] = session_override
    app.dependency_overrides[get_settings] = lambda: Settings(
        interface_webhook_secret="interface-secret", max_webhook_body_bytes=1024
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_interface_webhook_accepts_and_reports_duplicate(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_insert(*_args: object, **_kwargs: object) -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    monkeypatch.setattr(api, "insert_inbox_event", fake_insert)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "interface-secret"}
    first = client.post("/welcome/webhook/", headers=headers, json={"update_id": 42, "message": {}})
    duplicate = client.post("/welcome/webhook/", headers=headers, json={"update_id": 42, "message": {}})

    assert first.status_code == 200
    assert first.json() == {"ok": True, "duplicate": False}
    assert duplicate.json() == {"ok": True, "duplicate": True}


def test_interface_webhook_rejects_bad_secret(client: TestClient) -> None:
    response = client.post("/welcome/webhook/", json={"update_id": 1})
    assert response.status_code == 403


def test_webhook_rejects_large_body(client: TestClient) -> None:
    response = client.post(
        "/welcome/webhook/",
        headers={"X-Telegram-Bot-Api-Secret-Token": "interface-secret"},
        json={"update_id": 1, "padding": "x" * 2048},
    )
    assert response.status_code == 413


def test_webhook_rejects_invalid_update(client: TestClient) -> None:
    response = client.post(
        "/welcome/webhook/",
        headers={"X-Telegram-Bot-Api-Secret-Token": "interface-secret"},
        json={"message": {}},
    )
    assert response.status_code == 400


def test_client_webhook_validates_both_secrets(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    public_id = uuid.uuid4()

    async def fake_bot(*_args: object, **_kwargs: object) -> BotWebhookIdentity:
        return BotWebhookIdentity(7, public_id, "path-secret", "header-secret")

    async def fake_insert(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(api, "find_active_bot", fake_bot)
    monkeypatch.setattr(api, "insert_inbox_event", fake_insert)
    response = client.post(
        f"/welcome/client/{public_id}/path-secret/",
        headers={"X-Telegram-Bot-Api-Secret-Token": "header-secret"},
        json={"update_id": 99},
    )
    assert response.status_code == 200

    wrong_path = client.post(
        f"/welcome/client/{public_id}/wrong/",
        headers={"X-Telegram-Bot-Api-Secret-Token": "header-secret"},
        json={"update_id": 99},
    )
    assert wrong_path.status_code == 404


def test_database_outage_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable(*_args: object, **_kwargs: object) -> bool:
        raise OperationalError("insert", {}, RuntimeError("offline"))

    monkeypatch.setattr(api, "insert_inbox_event", unavailable)
    response = client.post(
        "/welcome/webhook/",
        headers={"X-Telegram-Bot-Api-Secret-Token": "interface-secret"},
        json={"update_id": 1},
    )
    assert response.status_code == 503


def test_live_probe_does_not_depend_on_database(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
