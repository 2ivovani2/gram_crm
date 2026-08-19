from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

from gramly_welcome.config import Settings, get_settings
from gramly_welcome.db import session_dependency
from gramly_welcome.main import app


class FakeSession:
    pass


@pytest.fixture
def client() -> Iterator[TestClient]:
    async def session_override() -> AsyncIterator[FakeSession]:
        yield FakeSession()

    app.dependency_overrides[session_dependency] = session_override
    app.dependency_overrides[get_settings] = lambda: Settings(
        interface_bot_username="GramlyHelloBot"
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
