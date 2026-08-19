from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

from gramly_welcome import api
from gramly_welcome.advertising import (
    AdCreativeInput,
    advertising_payload,
    validate_ad_creative,
)
from gramly_welcome.db import session_dependency
from gramly_welcome.main import app
from gramly_welcome.models import AdCreative


def creative(**overrides: object) -> AdCreative:
    values: dict[str, object] = {
        "name": "Default",
        "text": "Приветствие отправлено с GramlyHello",
        "entities": [],
        "cta_text": "Попробовать",
        "cta_url": "https://gramly.tech",
        "weight": 1,
        "is_active": True,
    }
    values.update(overrides)
    return AdCreative(**values)


def test_advertising_payload_uses_opaque_tracking_url() -> None:
    token = uuid.UUID("2a845b29-739c-4e08-9ae3-c78dc874f187")

    payload = advertising_payload(creative(), token, "https://gramly.tech/welcome/")

    assert payload["text"] == "Приветствие отправлено с GramlyHello"
    button = payload["keyboard"]["rows"][0][0]
    assert button["value"] == ("https://gramly.tech/welcome/ad/2a845b29-739c-4e08-9ae3-c78dc874f187")
    assert "https://gramly.tech" not in button["value"].removeprefix("https://gramly.tech/welcome/ad/")


def test_advertising_payload_without_cta_has_no_keyboard() -> None:
    payload = advertising_payload(
        creative(cta_text="", cta_url=""), uuid.uuid4(), "https://gramly.tech/welcome"
    )

    assert "keyboard" not in payload


def test_advertising_definition_requires_complete_safe_cta() -> None:
    with pytest.raises(ValueError, match="together"):
        validate_ad_creative(AdCreativeInput(name="Promo", text="Text", entities=[], cta_text="Open"))
    with pytest.raises(ValueError, match="HTTP"):
        validate_ad_creative(
            AdCreativeInput(
                name="Promo",
                text="Text",
                entities=[],
                cta_text="Open",
                cta_url="javascript:alert(1)",
            )
        )


class FakeSession:
    pass


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    async def session_override() -> AsyncIterator[FakeSession]:
        yield FakeSession()

    app.dependency_overrides[session_dependency] = session_override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_ad_click_records_before_redirect(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_record(*_args: object) -> str:
        return "https://gramly.tech"

    monkeypatch.setattr(api, "record_ad_click", fake_record)
    response = api_client.get(
        "/welcome/ad/2a845b29-739c-4e08-9ae3-c78dc874f187",
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers["location"] == "https://gramly.tech"
