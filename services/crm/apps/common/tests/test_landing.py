from django.test import override_settings
from django.urls import reverse


@override_settings(
    TELEGRAM_BOT_USERNAME="@gramly_crm_bot",
    GRAMLY_HELLO_BOT_USERNAME="@gramly_welcome_bot",
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
    assert "crm.gramly.tech/vpn-probe/" in response.content.decode()
    assert "Туннель или SSO-сессия неактивны" in response.content.decode()
    assert "Приватный маршрут не отвечает" in response.content.decode()
    assert "Проверить ещё раз" in response.content.decode()


@override_settings(PUBLIC_ACCESS_GATE=True)
def test_public_access_gate_intercepts_hello_admin_root(client):
    response = client.get("/", HTTP_HOST="hello-admin.gramly.tech")

    assert response.status_code == 403
    assert "Сначала включите" in response.content.decode()
    assert "GramlyHello Control" in response.content.decode()
    assert "crm.gramly.tech/vpn-probe/" in response.content.decode()


@override_settings(PUBLIC_ACCESS_GATE=True, ALLOWED_HOSTS=["crm.gramly.tech"])
def test_public_vpn_probe_bypasses_gate_and_reports_public_runtime(client):
    response = client.get(reverse("vpn-probe"), HTTP_HOST="crm.gramly.tech")

    assert response.status_code == 200
    assert response.json() == {"private": False}
    assert response["Access-Control-Allow-Origin"] == "*"
    assert response["Cache-Control"] == "no-store"


@override_settings(PUBLIC_ACCESS_GATE=False, ALLOWED_HOSTS=["crm.gramly.tech"])
def test_private_vpn_probe_reports_private_runtime(client):
    response = client.get(reverse("vpn-probe"), HTTP_HOST="crm.gramly.tech")

    assert response.status_code == 200
    assert response.json() == {"private": True}
    assert response["Access-Control-Allow-Origin"] == "*"
    assert response["Cache-Control"] == "no-store"


@override_settings(PUBLIC_ACCESS_GATE=True, ALLOWED_HOSTS=["crm.gramly.tech"])
def test_vpn_probe_preflight_is_public_and_not_cached(client):
    response = client.options(reverse("vpn-probe"), HTTP_HOST="crm.gramly.tech")

    assert response.status_code == 204
    assert response["Access-Control-Allow-Origin"] == "*"
    assert response["Access-Control-Allow-Methods"] == "GET, OPTIONS"
    assert response["Cache-Control"] == "no-store"


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
