from __future__ import annotations

import pytest

from gramly_welcome.audience import (
    build_subscribe_token,
    captcha_callback,
    client_message_text,
    verify_subscribe_token,
)
from gramly_welcome.broadcasts import validate_broadcast_content
from gramly_welcome.content_service import ContentValidationError


def test_subscribe_token_is_bound_to_step_and_bot_secret() -> None:
    token = build_subscribe_token(42, 17, "bot-specific-secret")

    assert verify_subscribe_token(token, "bot-specific-secret") == (42, 17)
    assert verify_subscribe_token(token, "another-bot-secret") is None
    assert verify_subscribe_token(token.replace("42", "43", 1), "bot-specific-secret") is None
    assert verify_subscribe_token(token.replace("17", "18", 1), "bot-specific-secret") is None
    assert verify_subscribe_token("sub_invalid", "bot-specific-secret") is None


def test_captcha_callback_and_service_messages_are_stable() -> None:
    assert captcha_callback("challenge-id", "🦖") == "consent:challenge-id:🦖"
    assert "/stop" in client_message_text({"kind": "consent_confirmed"})
    assert "отписались" in client_message_text({"kind": "unsubscribed"})


def test_broadcast_keeps_rich_text_buttons_and_link_preview_setting() -> None:
    content = {
        "payload": {
            "type": "text",
            "text": "Привет ✨",
            "entities": [
                {
                    "type": "custom_emoji",
                    "offset": 7,
                    "length": 1,
                    "custom_emoji_id": "5368324170671202286",
                }
            ],
            "link_preview_enabled": False,
        },
        "attachments": [],
        "keyboard": {
            "kind": "inline",
            "rows": [
                [
                    {
                        "text": "Открыть",
                        "action_type": "url",
                        "value": "https://gramly.tech",
                        "style": "primary",
                    },
                    {
                        "text": "Позже",
                        "action_type": "callback",
                        "value": "later",
                        "style": "danger",
                    },
                ]
            ],
        },
    }

    normalized = validate_broadcast_content(content)

    assert normalized["payload"]["link_preview_enabled"] is False
    assert normalized["payload"]["entities"][0]["custom_emoji_id"] == "5368324170671202286"
    assert [button["style"] for button in normalized["keyboard"]["rows"][0]] == [
        "primary",
        "danger",
    ]


def test_empty_broadcast_is_rejected() -> None:
    with pytest.raises(ContentValidationError, match="Добавьте текст или файл"):
        validate_broadcast_content(
            {"payload": {"type": "text", "text": ""}, "attachments": [], "keyboard": None}
        )
