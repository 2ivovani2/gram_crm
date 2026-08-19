from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, WebAppInfo

from gramly_welcome.bot_ui import (
    DEFAULT_PREMIUM_EMOJI,
    BotUiTheme,
    inline_button,
    plain_markup,
    premium_text,
)
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


def test_inline_button_supports_telegram_mini_app() -> None:
    button = inline_button(
        "Открыть кабинет",
        web_app=WebAppInfo(url="https://hello.gramly.tech/app/"),
    )

    assert button.web_app is not None
    assert button.web_app.url == "https://hello.gramly.tech/app/"


def test_default_owner_button_uses_semantic_premium_icon_without_unicode_prefix() -> None:
    button = inline_button("📊 Аналитика", callback_data="menu:analytics")

    assert button.text == "Аналитика"
    assert button.icon_custom_emoji_id == DEFAULT_PREMIUM_EMOJI["analytics"]
    assert button.style is None


def test_owner_message_replaces_decorative_unicode_with_one_custom_emoji() -> None:
    text = premium_text("✅ <b>Готово</b>\n\n⚠️ Проверьте канал.", "success")

    assert text.startswith(f'<tg-emoji emoji-id="{DEFAULT_PREMIUM_EMOJI["success"]}">✅</tg-emoji>')
    assert text.count("<tg-emoji") == 1
    assert "⚠️" not in text
    assert "Проверьте канал" in text
