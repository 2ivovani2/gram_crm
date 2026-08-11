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
