from __future__ import annotations

from copy import deepcopy

import pytest

from gramly_welcome.delays import MAX_DELAY_SECONDS, DelayParseError, format_delay_clock, parse_delay
from gramly_welcome.personalization import personalize_operation, personalize_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0),
        ("45", 45),
        ("05:30", 330),
        ("1:02:03", 3_723),
        ("2:03:04:05", 183_845),
        (" 00 : 25 : 00 : 00 ", 90_000),
        ("180:00:00:00", MAX_DELAY_SECONDS),
    ],
)
def test_delay_parser_right_aligns_and_normalizes(raw: str, expected: int) -> None:
    assert parse_delay(raw) == expected


@pytest.mark.parametrize("raw", ["", ":", "1:2:3:4:5", "1:-2", "one", "180:00:00:01"])
def test_delay_parser_rejects_invalid_or_excessive_values(raw: str) -> None:
    with pytest.raises(DelayParseError):
        parse_delay(raw)


def test_delay_formatter_uses_canonical_four_part_clock() -> None:
    assert format_delay_clock(90_000) == "01:01:00:00"


def test_name_placeholder_is_optional_and_persisted_payload_is_immutable() -> None:
    payload = {"text": "Добро пожаловать!", "entities": []}
    original = deepcopy(payload)
    rendered, media = personalize_operation(payload, [], "Александр")
    assert rendered["text"] == "Добро пожаловать!"
    assert payload == original
    assert media == []


def test_name_is_replaced_everywhere_visible_but_not_in_button_url() -> None:
    payload = {
        "text": "{name}, привет, {name}!",
        "keyboard": {
            "rows": [[{"text": "Кабинет {name}", "action_type": "url", "value": "https://x.test/{name}"}]]
        },
    }
    rendered, _ = personalize_operation(payload, [], "Ира")
    assert rendered["text"] == "Ира, привет, Ира!"
    assert rendered["keyboard"]["rows"][0][0] == {
        "text": "Кабинет Ира",
        "action_type": "url",
        "value": "https://x.test/{name}",
    }


def test_empty_first_name_removes_only_exact_placeholder() -> None:
    rendered, _ = personalize_text("Привет, {name}! {{name}} {Name}", None, "")
    assert rendered == "Привет, ! {{name}} {Name}"


def test_utf16_entities_and_premium_emoji_offsets_are_recalculated() -> None:
    text = "👋 {name} ✨"
    entities = [
        {"type": "bold", "offset": 3, "length": 6},
        {"type": "custom_emoji", "offset": 10, "length": 1, "custom_emoji_id": "premium-1"},
    ]
    rendered, adjusted = personalize_text(text, entities, "Александр")
    assert rendered == "👋 Александр ✨"
    assert adjusted == [
        {"type": "bold", "offset": 3, "length": 9},
        {"type": "custom_emoji", "offset": 13, "length": 1, "custom_emoji_id": "premium-1"},
    ]


def test_caption_and_media_group_items_are_personalized() -> None:
    payload = {"items": [{"caption": "Фото для {name}", "caption_entities": []}]}
    media = [{"payload": {"caption": "Видео для {name}", "caption_entities": []}}]
    rendered, rendered_media = personalize_operation(payload, media, "Макс")
    assert rendered["items"][0]["caption"] == "Фото для Макс"
    assert rendered_media[0]["payload"]["caption"] == "Видео для Макс"
