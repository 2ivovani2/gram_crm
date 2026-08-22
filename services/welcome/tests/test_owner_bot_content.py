from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from gramly_welcome import owner_bot
from gramly_welcome.content_service import ContentValidationError, validate_step_keyboard
from gramly_welcome.models import Payment, Plan
from gramly_welcome.owner_bot import _parse_keyboard_definition, _toggle_channel_assignment
from gramly_welcome.telegram_delivery import _reply_markup


def test_keyboard_parser_uses_friendly_url_format() -> None:
    keyboard = _parse_keyboard_definition(
        "Открыть сайт - https://gramly.tech\n"
        "Telegram - https://t.me/gramly | Поддержка - http://example.com/help"
    )

    assert keyboard is not None
    assert keyboard["kind"] == "inline"
    assert [len(row) for row in keyboard["rows"]] == [1, 2]
    assert keyboard["rows"][1][0] == {
        "text": "Telegram",
        "action_type": "url",
        "value": "https://t.me/gramly",
        "style": "default",
    }


def test_keyboard_parser_accepts_fifteen_rows_and_forty_five_buttons() -> None:
    raw = "\n".join(
        " | ".join(f"Кнопка {row}-{button} - https://example.com/{row}/{button}" for button in range(3))
        for row in range(15)
    )

    keyboard = _parse_keyboard_definition(raw)

    assert keyboard is not None
    assert len(keyboard["rows"]) == 15
    assert sum(len(row) for row in keyboard["rows"]) == 45


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        (
            "Один - https://example.com | Два - https://example.com | "
            "Три - https://example.com | Четыре - https://example.com",
            "Ряд 1: можно добавить не более 3 кнопок",
        ),
        (
            "\n".join(f"Кнопка - https://example.com/{index}" for index in range(16)),
            "Можно добавить не более 15 рядов",
        ),
        ("Сайт https://example.com", "Ряд 1, кнопка 1: используйте формат"),
        ("Сайт - https://example.com |", "Ряд 1, кнопка 2: уберите лишний символ"),
        ("url | Сайт | https://gramly.tech", "Ряд 1, кнопка 1: используйте формат"),
    ],
)
def test_keyboard_parser_rejects_invalid_layout(raw: str, error: str) -> None:
    with pytest.raises(ContentValidationError, match=error):
        _parse_keyboard_definition(raw)


@pytest.mark.parametrize(
    "url",
    [
        "gramly.tech",
        "ftp://gramly.tech/file",
        "tg://resolve?domain=gramly",
        "https:///missing-host",
        "https://bad host.example",
        "https://-bad.example",
        "https://example.com:invalid",
    ],
)
def test_keyboard_parser_rejects_invalid_url(url: str) -> None:
    with pytest.raises(ContentValidationError, match="Ряд 1, кнопка 1"):
        _parse_keyboard_definition(f"Сайт - {url}")


def test_keyboard_parser_rejects_long_values() -> None:
    with pytest.raises(ContentValidationError, match="название длиннее 128"):
        _parse_keyboard_definition(f"{'К' * 129} - https://example.com")
    with pytest.raises(ContentValidationError, match="ссылка длиннее 1024"):
        _parse_keyboard_definition(f"Сайт - https://example.com/{'a' * 1005}")


def test_application_validator_rejects_api_layout_bypass() -> None:
    with pytest.raises(ContentValidationError, match="не более 3 кнопок"):
        validate_step_keyboard(
            {
                "kind": "inline",
                "rows": [
                    [
                        {"text": str(index), "action_type": "url", "value": "https://example.com"}
                        for index in range(4)
                    ]
                ],
            }
        )


def test_keyboard_parser_supports_removal() -> None:
    assert _parse_keyboard_definition("  УДАЛИТЬ ") is None


@pytest.mark.asyncio
async def test_button_handler_keeps_state_for_non_text_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers: list[str] = []

    class FakeState:
        async def get_data(self) -> dict[str, int]:
            return {"version_id": 10, "step_id": 20}

        async def clear(self) -> None:
            raise AssertionError("Invalid input must keep the FSM state")

    async def fake_answer(_message: Any, text: str, **_kwargs: Any) -> None:
        answers.append(text)

    monkeypatch.setattr(owner_bot, "owner_answer", fake_answer)

    await owner_bot.receive_chain_buttons(
        SimpleNamespace(text=None),
        SimpleNamespace(id=1),
        FakeState(),
    )

    assert "текстовым сообщением" in answers[0]


@pytest.mark.asyncio
async def test_button_handler_reports_saved_row_and_button_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers: list[str] = []
    saved: list[dict[str, Any] | None] = []

    class FakeState:
        cleared = False

        async def get_data(self) -> dict[str, int]:
            return {"version_id": 10, "step_id": 20}

        async def clear(self) -> None:
            self.cleared = True

    class FakeSessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def fake_replace(
        _session: object, _owner_id: int, _step_id: int, keyboard: dict[str, Any] | None
    ) -> None:
        saved.append(keyboard)

    async def fake_answer(_message: Any, text: str, **_kwargs: Any) -> None:
        answers.append(text)

    async def fake_editor(_message: Any, _owner: Any, _version_id: int) -> None:
        return None

    state = FakeState()
    monkeypatch.setattr(owner_bot, "session_factory", lambda: FakeSessionContext())
    monkeypatch.setattr(owner_bot, "replace_step_keyboard", fake_replace)
    monkeypatch.setattr(owner_bot, "owner_answer", fake_answer)
    monkeypatch.setattr(owner_bot, "send_chain_editor", fake_editor)

    await owner_bot.receive_chain_buttons(
        SimpleNamespace(
            text="Сайт - https://example.com\nTelegram - https://t.me/gramly | Помощь - https://example.com/help"
        ),
        SimpleNamespace(id=1),
        state,
    )

    assert state.cleared is True
    assert saved and saved[0] is not None
    assert "Сохранено: 3 кнопки в 2 рядах" in answers[0]


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


def test_delivery_keeps_existing_legacy_keyboards() -> None:
    callback_markup = _reply_markup(
        {
            "kind": "inline",
            "rows": [
                [
                    {
                        "text": "Дальше",
                        "action_type": "callback",
                        "value": "next",
                        "style": "success",
                    },
                    {
                        "text": "Telegram",
                        "action_type": "url",
                        "value": "tg://resolve?domain=gramly",
                    },
                ]
            ],
        }
    )
    reply_markup = _reply_markup(
        {
            "kind": "reply",
            "rows": [[{"text": "Продолжить", "action_type": "text", "value": "Продолжить"}]],
        }
    )

    assert isinstance(callback_markup, InlineKeyboardMarkup)
    assert callback_markup.inline_keyboard[0][0].callback_data == "next"
    assert callback_markup.inline_keyboard[0][1].url == "tg://resolve?domain=gramly"
    assert isinstance(reply_markup, ReplyKeyboardMarkup)
    assert reply_markup.keyboard[0][0].text == "Продолжить"


def test_assignment_toggle_reports_concrete_result_and_preserves_selection() -> None:
    selected, message = _toggle_channel_assignment("selected", {10, 20}, 20)
    assert selected == {10}
    assert message == "Назначение снято"

    selected, message = _toggle_channel_assignment("selected", selected, 30)
    assert selected == {10, 30}
    assert message == "Канал назначен"

    selected, message = _toggle_channel_assignment("all", {10, 30}, 40)
    assert selected == {40}
    assert message == "Канал назначен"


@pytest.mark.asyncio
async def test_owner_stars_checkout_exports_recurring_invoice_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class FakeBot:
        async def create_invoice_link(self, **kwargs: object) -> str:
            calls.update(kwargs)
            return "https://t.me/$invoice"

    monkeypatch.setattr(owner_bot, "interface_bot", lambda: FakeBot())
    checkout_token = uuid.uuid4()
    payment = Payment(
        checkout_token=checkout_token,
        owner_id=7,
        plan_id=2,
        provider="telegram_stars",
        amount_rub=Decimal("419"),
        original_amount=Decimal("400"),
    )
    plan = Plan(price_xtr=400)

    url = await owner_bot._create_owner_stars_invoice_link(payment, plan)

    assert url == "https://t.me/$invoice"
    assert calls["subscription_period"] == 2_592_000
    assert calls["currency"] == "XTR"
    assert calls["payload"] == str(checkout_token)
