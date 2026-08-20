from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from aiogram.types import InlineKeyboardMarkup

from gramly_welcome import owner_bot
from gramly_welcome.content_service import ContentValidationError
from gramly_welcome.models import Payment, Plan
from gramly_welcome.owner_bot import _parse_keyboard_definition, _toggle_channel_assignment
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
