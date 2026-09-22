from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup

from gramly_welcome import owner_bot
from gramly_welcome.content_service import (
    ContentValidationError,
    _validate_flow_limits,
    validate_step_keyboard,
)
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
async def test_button_handler_opens_color_wizard_before_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers: list[str] = []
    saved: list[dict[str, Any] | None] = []

    class FakeState:
        selected_state: Any = None
        data: dict[str, Any] = {"version_id": 10, "step_id": 20}

        async def get_data(self) -> dict[str, Any]:
            return self.data

        async def update_data(self, **values: Any) -> None:
            self.data.update(values)

        async def set_state(self, value: Any) -> None:
            self.selected_state = value

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

    state = FakeState()
    monkeypatch.setattr(owner_bot, "session_factory", lambda: FakeSessionContext())
    monkeypatch.setattr(owner_bot, "replace_step_keyboard", fake_replace)
    monkeypatch.setattr(owner_bot, "owner_answer", fake_answer)

    await owner_bot.receive_chain_buttons(
        SimpleNamespace(
            text="Сайт - https://example.com\nTelegram - https://t.me/gramly | Помощь - https://example.com/help"
        ),
        SimpleNamespace(id=1),
        state,
    )

    assert state.selected_state == owner_bot.WelcomeButtonState.choosing_colors
    assert saved == []
    assert state.data["button_color_index"] == 0
    assert len(state.data["pending_keyboard"]["rows"]) == 2
    assert "Цвет кнопки 1 из 3" in answers[0]


def test_button_color_picker_exposes_four_styles_and_navigation() -> None:
    keyboard = _parse_keyboard_definition(
        "Сайт - https://example.com | Telegram - https://t.me/gramly"
    )
    assert keyboard is not None

    text, markup = owner_bot._button_color_picker(keyboard, 0)
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    ]

    assert "Цвет кнопки 1 из 2" in text
    for style in ("default", "primary", "success", "danger"):
        assert f"chain:button-style:{style}" in callbacks
    assert "chain:button-style-next" in callbacks
    assert "chain:button-style-save" in callbacks
    assert "chain:button-style-cancel" in callbacks

    final_text, _final_markup = owner_bot._button_color_picker(keyboard, 1)
    assert "Итоговый предпросмотр" in final_text
    assert "Сайт" in final_text and "Telegram" in final_text


def test_legacy_button_color_suffixes_keep_individual_styles() -> None:
    keyboard = _parse_keyboard_definition(
        "Обычная - https://example.com - стандартный | "
        "Основная - https://example.com/blue - синий | "
        "Готово - https://example.com/green - зелёный\n"
        "Удалить - https://example.com/red - красный"
    )

    assert keyboard is not None
    assert [button["style"] for row in keyboard["rows"] for button in row] == [
        "default",
        "primary",
        "success",
        "danger",
    ]


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


def test_composer_summary_counts_text_and_files() -> None:
    summary = owner_bot._composer_summary(
        {
            "composer_items": [
                {"kind": "message", "payload": {"type": "text", "text": "Один"}},
                {"kind": "attachment", "attachment_index": 0},
                {"kind": "attachment", "attachment_index": 1},
            ],
            "composer_attachments": [{"storage_key": "one"}, {"storage_key": "two"}],
        }
    )

    assert "1</b> текстовых" in summary
    assert "2</b> файлов" in summary


@pytest.mark.parametrize(
    ("steps", "files", "error"),
    [(6, 0, "не более 5 сообщений"), (1, 6, "не более 5 файлов")],
)
def test_flow_limits_are_shared_by_welcome_and_farewell(
    steps: int, files: int, error: str
) -> None:
    with pytest.raises(ContentValidationError, match=error):
        _validate_flow_limits(steps, files)

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


def test_imported_url_keyboard_preserves_safe_titles_and_rows() -> None:
    message = Message(
        message_id=1, date=datetime.now(UTC), chat=Chat(id=7, type="private"), text="Привет",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть сайт", url="https://gramly.tech"),
        ]]),
    )
    result = owner_bot._import_url_keyboard(message)
    assert result is not None
    assert result["rows"][0][0]["value"] == "https://gramly.tech"
    assert result["rows"][0][0]["text"] == "Открыть сайт"
    assert result["rows"][0][0]["action_type"] == "url"


def test_import_keeps_url_style_without_copying_foreign_actions() -> None:
    message = Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=7, type="private"), text="Привет",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Сайт", url="https://gramly.tech", style="success"),
            InlineKeyboardButton(text="Удалить", callback_data="foreign:delete"),
        ]]))
    result = owner_bot._import_url_keyboard(message)
    assert result and result["rows"] == [[{"text":"Сайт","action_type":"url","value":"https://gramly.tech","style":"success"}]]


@pytest.mark.parametrize("button", [
    InlineKeyboardButton(text="Другой бот", callback_data="foreign:delete"),
    InlineKeyboardButton(text="Ссылка", url="tg://user?id=7"),
])
def test_foreign_callbacks_and_non_http_urls_are_not_imported(button: InlineKeyboardButton) -> None:
    message = Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=7, type="private"), text="Привет",
                      reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]]))
    if button.callback_data:
        assert owner_bot._import_url_keyboard(message) is None
    else:
        with pytest.raises(ContentValidationError):
            owner_bot._import_url_keyboard(message)


@pytest.mark.asyncio
async def test_composer_back_retains_collected_items(monkeypatch: pytest.MonkeyPatch) -> None:
    class State:
        selected: object = None

        async def set_state(self, state: object) -> None:
            self.selected = state

        async def get_data(self) -> dict[str, object]:
            return {"composer_items": [{"kind": "message", "payload": {"text": "Draft"}}]}

    async def answer(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(owner_bot, "owner_answer", answer)
    state = State()
    callback = SimpleNamespace(message=object(), answer=answer)
    await owner_bot.back_to_composer(callback, state)
    assert state.selected == owner_bot.WelcomeMessageState.waiting_for_message
    assert (await state.get_data())["composer_items"]
