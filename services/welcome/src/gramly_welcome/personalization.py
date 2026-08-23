from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

PLACEHOLDER = "{name}"
PLACEHOLDER_PATTERN = re.compile(r"(?<!\{)\{name\}(?!\})")


def _replace_name(value: str, replacement: str) -> str:
    return PLACEHOLDER_PATTERN.sub(lambda _match: replacement, value)


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _replacement_spans(value: str, replacement: str) -> list[tuple[int, int, int, int]]:
    spans: list[tuple[int, int, int, int]] = []
    for match in PLACEHOLDER_PATTERN.finditer(value):
        start, end = match.span()
        spans.append(
            (
                _utf16_length(value[:start]),
                _utf16_length(value[:end]),
                _utf16_length(_replace_name(value[:start], replacement)),
                _utf16_length(_replace_name(value[:start], replacement) + replacement),
            )
        )
    return spans


def _map_boundary(
    position: int, spans: list[tuple[int, int, int, int]], *, end_boundary: bool
) -> int:
    shift = 0
    for old_start, old_end, new_start, new_end in spans:
        if position <= old_start:
            return position + shift
        if position >= old_end:
            shift = new_end - old_end
            if position == old_end:
                return new_end
            continue
        return new_end if end_boundary else new_start
    return position + shift


def personalize_text(
    value: str, entities: list[dict[str, Any]] | None, first_name: str
) -> tuple[str, list[dict[str, Any]] | None]:
    if not PLACEHOLDER_PATTERN.search(value):
        return value, deepcopy(entities) if entities else entities
    replacement = first_name or ""
    spans = _replacement_spans(value, replacement)
    rendered = _replace_name(value, replacement)
    if not entities:
        return rendered, entities
    adjusted: list[dict[str, Any]] = []
    for raw in entities:
        entity = deepcopy(raw)
        start = int(entity.get("offset") or 0)
        end = start + int(entity.get("length") or 0)
        mapped_start = _map_boundary(start, spans, end_boundary=False)
        mapped_end = _map_boundary(end, spans, end_boundary=True)
        entity["offset"] = mapped_start
        entity["length"] = max(0, mapped_end - mapped_start)
        adjusted.append(entity)
    return rendered, adjusted


def personalize_operation(
    payload: dict[str, Any], media: list[dict[str, Any]], first_name: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Render visible Telegram text while keeping persisted operation snapshots immutable."""

    result = deepcopy(payload)
    rendered_media = deepcopy(media)

    def render(container: dict[str, Any], text_key: str, entities_key: str) -> None:
        value = container.get(text_key)
        if not isinstance(value, str):
            return
        entities = container.get(entities_key)
        text, updated = personalize_text(
            value,
            entities if isinstance(entities, list) else None,
            first_name,
        )
        container[text_key] = text
        if isinstance(entities, list):
            container[entities_key] = updated

    render(result, "text", "entities")
    render(result, "caption", "caption_entities")
    for item in result.get("items") or []:
        if isinstance(item, dict):
            render(item, "caption", "caption_entities")
    keyboard = result.get("keyboard")
    if isinstance(keyboard, dict):
        for row in keyboard.get("rows") or []:
            if isinstance(row, list):
                for button in row:
                    if isinstance(button, dict) and isinstance(button.get("text"), str):
                        button["text"] = _replace_name(button["text"], first_name or "")
    for item in rendered_media:
        if not isinstance(item, dict):
            continue
        item_payload = item.get("payload")
        if isinstance(item_payload, dict):
            render(item_payload, "caption", "caption_entities")
    return result, rendered_media
