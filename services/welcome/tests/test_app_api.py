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
from gramly_welcome.web_sessions import AuthenticatedWebSession, CreatedWebSession


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
    cookie, csrf_cookie = response.headers.get_list("set-cookie")
    assert "custom_welcome_cookie=browser-secret" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    assert "gramly_hello_csrf=csrf-secret" in csrf_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "Secure" in csrf_cookie
    assert "SameSite=strict" in csrf_cookie
    assert "Path=/" in csrf_cookie
    assert "Max-Age=" in csrf_cookie


def test_logout_validates_session_csrf_and_clears_both_cookies(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = AuthenticatedWebSession(
        session_id=uuid.uuid4(),
        owner=Owner(id=7, telegram_id=42, first_name="Alex"),
        expires_at=datetime.now(UTC) + timedelta(hours=12),
    )
    revoked: list[uuid.UUID] = []

    async def valid_csrf(_session: object, session_id: uuid.UUID, token: str) -> bool:
        return session_id == auth.session_id and token == "current-session-csrf"

    async def revoke(_session: object, session_id: uuid.UUID) -> None:
        revoked.append(session_id)

    app.dependency_overrides[app_api.required_channel_user] = lambda: app_api.CurrentWebUser(auth)
    monkeypatch.setattr(app_api, "csrf_token_valid", valid_csrf)
    monkeypatch.setattr(app_api, "revoke_web_session", revoke)
    # Merely sending the readable cookie cannot authorize a mutation.
    app_client.cookies.set("gramly_hello_csrf", "current-session-csrf")
    missing = app_client.post("/api/v1/session/logout")
    assert missing.status_code == 422
    stale = app_client.post("/api/v1/session/logout", headers={"X-CSRF-Token": "old-session-csrf"})
    assert stale.status_code == 403
    assert stale.json()["detail"] == "Invalid CSRF token"
    assert revoked == []
    response = app_client.post("/api/v1/session/logout", headers={"X-CSRF-Token": "current-session-csrf"})
    assert response.status_code == 204
    assert revoked == [auth.session_id]
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert any(cookie.startswith("custom_welcome_cookie=") for cookie in cookies)
    assert any(cookie.startswith("gramly_hello_csrf=") for cookie in cookies)
    assert all("Max-Age=0" in cookie for cookie in cookies)


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
