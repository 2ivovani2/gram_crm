import json

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.welcome_bots.models import ManagedBot, Owner


class ClientWebhookSecurityTests(TestCase):
    def setUp(self):
        owner = Owner.objects.create(telegram_id=42)
        self.bot = ManagedBot.objects.create(
            owner=owner,
            telegram_id=1234,
            display_name="Test",
            token_ciphertext="unused",
            path_secret="path-secret",
            webhook_secret="header-secret",
        )
        self.url = reverse(
            "welcome-client-webhook",
            kwargs={"public_id": self.bot.public_id, "path_secret": self.bot.path_secret},
        )

    def test_rejects_missing_or_wrong_header(self):
        response = self.client.post(self.url, data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="wrong",
        )
        self.assertEqual(response.status_code, 403)

    def test_hides_invalid_path(self):
        url = reverse(
            "welcome-client-webhook",
            kwargs={"public_id": self.bot.public_id, "path_secret": "not-the-secret"},
        )
        response = self.client.post(url, data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 404)

    def test_valid_security_layer_reaches_payload_validation(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"not": "an update"}),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="header-secret",
        )
        self.assertEqual(response.status_code, 400)


class InterfaceWebhookSecurityTests(TestCase):
    @override_settings(WELCOME_BOT_TOKEN="")
    def test_disabled_product_is_not_exposed(self):
        response = self.client.post(reverse("welcome-interface-webhook"), data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 404)

    @override_settings(WELCOME_BOT_TOKEN="123:test", WELCOME_WEBHOOK_SECRET="secret")
    def test_interface_requires_secret_header(self):
        response = self.client.post(reverse("welcome-interface-webhook"), data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)
