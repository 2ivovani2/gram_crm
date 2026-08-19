from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from gramly_welcome.bot_ui import BotUiTheme, inline_button, plain_markup
from gramly_welcome.learning import order_tip_ids
from gramly_welcome.workers.notifications import notification_text_and_entities


def test_tip_order_does_not_repeat_previous_first_tip() -> None:
    ordered = order_tip_ids([10, 20, 30], last_tip_id=10)

    assert sorted(ordered) == [10, 20, 30]
    assert ordered[0] != 10


def test_single_tip_remains_available_between_sessions() -> None:
    assert order_tip_ids([10], last_tip_id=10) == [10]


def test_notification_entities_shift_by_utf16_units() -> None:
    text, entities = notification_text_and_entities(
        {
            "title": "Обновление 🚀",
            "body": "🙂 совет",
            "entities": [
                {
                    "type": "custom_emoji",
                    "offset": 0,
                    "length": 2,
                    "custom_emoji_id": "5368324170671202286",
                }
            ],
        }
    )

    assert text == "Обновление 🚀\n\n🙂 совет"
    assert entities[0].type == "bold"
    assert entities[1].offset == len("Обновление 🚀\n\n".encode("utf-16-le")) // 2
    assert entities[1].custom_emoji_id == "5368324170671202286"


def test_colored_premium_button_has_plain_fallback() -> None:
    theme = BotUiTheme(
        enhanced=True,
        premium_emoji={"help": "5368324170671202286"},
    )
    enhanced = inline_button(
        "Помощь",
        callback_data="help:home",
        style="success",
        emoji_key="help",
        theme=theme,
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[[enhanced]])

    assert enhanced.style == "success"
    assert enhanced.icon_custom_emoji_id == "5368324170671202286"
    fallback = plain_markup(markup)
    assert fallback is not None
    assert fallback.inline_keyboard[0][0].style is None
    assert fallback.inline_keyboard[0][0].icon_custom_emoji_id is None
