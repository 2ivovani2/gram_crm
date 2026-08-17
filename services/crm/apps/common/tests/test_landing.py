from django.test import override_settings
from django.urls import reverse


@override_settings(
    TELEGRAM_BOT_USERNAME="@gramly_crm_bot",
    WELCOME_BOT_USERNAME="@gramly_welcome_bot",
)
def test_landing_promotes_welcome_bot(client):
    response = client.get(reverse("landing"))

    assert response.status_code == 200
    assert response.context["bot_username"] == "gramly_crm_bot"
    assert response.context["welcome_bot_username"] == "gramly_welcome_bot"
    assert b"https://t.me/gramly_welcome_bot" in response.content
    assert "Gramly Welcome" in response.content.decode()


@override_settings(PUBLIC_ACCESS_GATE=True)
def test_public_access_gate_intercepts_private_hostname(client):
    response = client.get("/crm/login/", HTTP_HOST="crm.gramly.tech")

    assert response.status_code == 403
    assert "Сначала включите" in response.content.decode()
    assert "Gramly CRM" in response.content.decode()


@override_settings(PUBLIC_ACCESS_GATE=False, ALLOWED_HOSTS=["crm.gramly.tech"])
def test_private_access_gate_is_disabled_for_crm_runtime(client):
    response = client.get("/", HTTP_HOST="crm.gramly.tech")

    assert response.status_code == 200
    assert "Gramly Welcome" in response.content.decode()


@override_settings(DEBUG=False, ALLOWED_HOSTS=["gramly.tech"])
def test_custom_not_found_page(client):
    response = client.get("/definitely-missing/", HTTP_HOST="gramly.tech")

    assert response.status_code == 404
    assert "Здесь ничего нет" in response.content.decode()


@override_settings(PUBLIC_ACCESS_GATE=True)
def test_public_surface_returns_branded_404_for_private_paths(client):
    response = client.get("/crm/login/", HTTP_HOST="gramly.tech")

    assert response.status_code == 404
    assert "Здесь ничего нет" in response.content.decode()


@override_settings(PUBLIC_ACCESS_GATE=True, ALLOWED_HOSTS=["gramly.tech"])
def test_public_surface_keeps_landing_available(client):
    response = client.get("/", HTTP_HOST="gramly.tech")

    assert response.status_code == 200
    assert "Gramly Welcome" in response.content.decode()
