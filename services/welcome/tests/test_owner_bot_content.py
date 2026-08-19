from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardMarkup

from gramly_welcome.content_service import ContentValidationError
from gramly_welcome.owner_bot import _parse_keyboard_definition
from gramly_welcome.telegram_delivery import _reply_markup


def test_keyboard_parser_preserves_rows_actions_and_styles() -> None:
    keyboard = _parse_keyboard_definition(
        "url | Сайт | https://gramly.tech | primary ;; callback | Дальше | next | success\n"
        "url | Помощь | tg://resolve?domain=gramly_support | danger"
    )

    assert keyboard is not None
    assert keyboard["kind"] == "inline"
    assert [len(row) for row in keyboard["rows"]] == [2, 1]
    assert keyboard["rows"][0][0]["style"] == "primary"
    assert keyboard["rows"][0][1]["value"] == "next"


def test_keyboard_parser_rejects_mixed_inline_and_reply() -> None:
    with pytest.raises(ContentValidationError, match="нельзя смешивать"):
        _parse_keyboard_definition("url | Сайт | https://gramly.tech\nreply | Продолжить")


def test_keyboard_parser_supports_removal() -> None:
    assert _parse_keyboard_definition("  УДАЛИТЬ ") is None


def test_reply_markup_keeps_functionality_when_button_styles_are_unsupported() -> None:
    markup = _reply_markup(
        {
            "kind": "inline",
            "rows": [
                [
                    {
                        "text": "Сайт",
                        "action_type": "url",
                        "value": "https://gramly.tech",
                        "style": "primary",
                    }
                ]
            ],
        }
    )

    assert isinstance(markup, InlineKeyboardMarkup)
    assert markup.inline_keyboard[0][0].url == "https://gramly.tech"
