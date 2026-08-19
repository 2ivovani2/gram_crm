from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

from gramly_welcome.config import Settings, get_settings
from gramly_welcome.db import session_dependency
from gramly_welcome.main import app


class FakeSession:
    async def scalars(self, _statement: object) -> FakeScalarResult:
        return FakeScalarResult()


class FakeScalarResult:
    def all(self) -> list[object]:
        return []


@pytest.fixture
def client() -> Iterator[TestClient]:
    async def session_override() -> AsyncIterator[FakeSession]:
        yield FakeSession()

    app.dependency_overrides[session_dependency] = session_override
    app.dependency_overrides[get_settings] = lambda: Settings(
        interface_bot_username="GramlyHelloBot",
        interface_bot_token="telegram-secret",
        crypto_pay_api_token="crypto-secret",
        crypto_pay_webhook_secret="path-secret",
        crypto_pay_api_base_url="https://pay.crypt.bot",
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_public_config_exposes_only_safe_interface_name(client: TestClient) -> None:
    response = client.get("/api/v1/public-config")

    assert response.status_code == 200
    assert response.json() == {"interface_bot_username": "GramlyHelloBot"}


def test_admin_api_rejects_request_without_forward_auth(client: TestClient) -> None:
    response = client.get("/api/admin/v1/overview")

    assert response.status_code == 403


def test_admin_api_rejects_non_owner_group_before_database_access(client: TestClient) -> None:
    response = client.get(
        "/api/admin/v1/overview",
        headers={
            "X-authentik-username": "employee",
            "X-authentik-groups": "business|crm-users",
        },
    )

    assert response.status_code == 403


def test_admin_mutation_requires_explicit_same_origin_confirmation(client: TestClient) -> None:
    response = client.post(
        "/api/admin/v1/manuals",
        headers={
            "X-authentik-username": "owner",
            "X-authentik-groups": "gramly-owners",
        },
        json={
            "slug": "welcome-basics",
            "title": "Welcome basics",
            "telegraph_url": "https://telegra.ph/welcome-basics",
        },
    )

    assert response.status_code == 403


def test_payment_readiness_exposes_status_but_not_secret_values(client: TestClient) -> None:
    response = client.get(
        "/api/admin/v1/payments/readiness",
        headers={
            "X-authentik-username": "owner",
            "X-authentik-groups": "gramly-owners",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["crypto_pay"] == {
        "api_token_configured": True,
        "webhook_secret_configured": True,
        "production_api": True,
    }
    assert payload["telegram_stars"] == {"interface_bot_configured": True}
    serialized = response.text
    assert "crypto-secret" not in serialized
    assert "path-secret" not in serialized
    assert "telegram-secret" not in serialized
