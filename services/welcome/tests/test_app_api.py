from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from gramly_welcome import app_api
from gramly_welcome.config import Settings, get_settings
from gramly_welcome.db import session_dependency
from gramly_welcome.main import app
from gramly_welcome.models import Owner, Payment, Plan
from gramly_welcome.telegram_auth import (
    TelegramMiniAppUser,
    VerifiedInitData,
)
from gramly_welcome.web_sessions import CreatedWebSession


class FakeSession:
    pass


@pytest.fixture
def app_client() -> Iterator[TestClient]:
    async def session_override() -> AsyncIterator[FakeSession]:
        yield FakeSession()

    app.dependency_overrides[session_dependency] = session_override
    app.dependency_overrides[get_settings] = lambda: Settings(
        interface_bot_token="123:secret",
        mini_app_cookie_name="custom_welcome_cookie",
        mini_app_cookie_secure=True,
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_telegram_session_sets_configured_httponly_cookie(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    verified = VerifiedInitData(
        user=TelegramMiniAppUser(id=42, first_name="Alex"),
        auth_date=now,
        query_id="query",
        start_param="",
    )
    owner = Owner(id=7, telegram_id=42, first_name="Alex")

    def fake_verify(*_args: object, **_kwargs: object) -> VerifiedInitData:
        return verified

    async def fake_create(*_args: object, **_kwargs: object) -> CreatedWebSession:
        return CreatedWebSession(
            token="browser-secret",
            csrf_token="csrf-secret",
            expires_at=now + timedelta(hours=12),
            owner=owner,
        )

    monkeypatch.setattr(app_api, "verify_init_data", fake_verify)
    monkeypatch.setattr(app_api, "create_web_session", fake_create)

    response = app_client.post("/api/v1/session/telegram", json={"init_data": "signed"})

    assert response.status_code == 200
    assert response.json()["csrf_token"] == "csrf-secret"
    cookie = response.headers["set-cookie"]
    assert "custom_welcome_cookie=browser-secret" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie


def test_logout_requires_an_authenticated_session(app_client: TestClient) -> None:
    response = app_client.post("/api/v1/session/logout", headers={"X-CSRF-Token": "missing-session"})
    assert response.status_code == 401


def test_me_requires_an_authenticated_session(app_client: TestClient) -> None:
    response = app_client.get("/api/v1/me")
    assert response.status_code == 401


def test_invalid_init_data_returns_401(app_client: TestClient) -> None:
    response = app_client.post("/api/v1/session/telegram", json={"init_data": "invalid"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_mini_app_stars_invoice_is_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class FakeSession:
        async def close(self) -> None:
            calls["closed"] = True

    class FakeBot:
        def __init__(self, token: str) -> None:
            calls["token"] = token
            self.session = FakeSession()

        async def create_invoice_link(self, **kwargs: object) -> str:
            calls.update(kwargs)
            return "https://t.me/invoice-link"

    monkeypatch.setattr(app_api, "Bot", FakeBot)
    checkout_token = uuid.uuid4()
    payment = Payment(
        checkout_token=checkout_token,
        owner_id=7,
        plan_id=2,
        provider="telegram_stars",
        amount_rub=Decimal("400"),
        original_amount=Decimal("400"),
    )
    plan = Plan(price_xtr=400)

    url = await app_api._create_stars_invoice_link(Settings(interface_bot_token="123:secret"), payment, plan)

    assert url == "https://t.me/invoice-link"
    assert calls["currency"] == "XTR"
    assert calls["provider_token"] == ""
    assert calls["subscription_period"] == 2_592_000
    assert calls["payload"] == str(checkout_token)
    assert calls["closed"] is True
