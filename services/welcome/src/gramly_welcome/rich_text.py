"""Telegram entity validation shared by both editors."""

from typing import Any

from aiogram.types import MessageEntity


def validate_rich_text(payload: dict[str, Any]) -> None:
    """Validate offsets before anything can reach Telegram; Python indexes differ."""
    for text_key, entities_key, maximum in (
        ("text", "entities", 4096),
        ("caption", "caption_entities", 1024),
    ):
        text = payload.get(text_key, "")
        if not isinstance(text, str) or len(text) > maximum:
            raise ValueError(f"Допустимо до {maximum} символов")
        boundaries = {0}
        offset = 0
        for char in text:
            offset += len(char.encode("utf-16-le")) // 2
            boundaries.add(offset)
        entities = payload.get(entities_key) or []
        if not isinstance(entities, list) or len(entities) > 100:
            raise ValueError("Слишком много фрагментов форматирования")
        for raw in entities:
            entity = MessageEntity.model_validate(raw)
            if (
                entity.length <= 0
                or entity.offset not in boundaries
                or entity.offset + entity.length not in boundaries
            ):
                raise ValueError("Некорректные границы форматирования")
            if entity.type == "custom_emoji" and not str(entity.custom_emoji_id or "").isdigit():
                raise ValueError("Некорректный Premium-эмодзи")
            if entity.type == "text_link" and not str(entity.url or "").startswith(
                ("https://", "http://", "tg://")
            ):
                raise ValueError("Некорректная ссылка в тексте")
