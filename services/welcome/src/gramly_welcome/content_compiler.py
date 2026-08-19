from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AttachmentSpec:
    media_type: str
    storage_key: str
    original_name: str = ""
    mime_type: str = ""
    size: int = 0
    payload: dict[str, Any] | None = None

    def as_media(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "media_type": self.media_type,
            "storage_key": self.storage_key,
            "original_name": self.original_name,
            "mime_type": self.mime_type,
            "size": self.size,
            "payload": payload,
        }


@dataclass(frozen=True)
class CompiledOperation:
    operation_type: str
    payload: dict[str, Any]
    media: list[dict[str, Any]]


def _group_family(media_type: str) -> str | None:
    if media_type in {"photo", "video"}:
        return "visual"
    if media_type == "audio":
        return "audio"
    if media_type == "document":
        return "document"
    return None


def _payloads_for_attachments(
    step_payload: dict[str, Any], attachments: list[AttachmentSpec]
) -> list[dict[str, Any]]:
    raw_items = step_payload.get("items")
    items = raw_items if isinstance(raw_items, list) else []
    result: list[dict[str, Any]] = []
    for index, attachment in enumerate(attachments):
        payload = dict(attachment.payload or {})
        if index < len(items) and isinstance(items[index], dict):
            payload = {**items[index], **payload}
        elif index == 0:
            for key in ("caption", "caption_entities", "has_spoiler"):
                if key in step_payload and key not in payload:
                    payload[key] = step_payload[key]
            if step_payload.get("type") == "text" and step_payload.get("text"):
                payload.setdefault("caption", step_payload["text"])
                payload.setdefault("caption_entities", step_payload.get("entities"))
        result.append(payload)
    return result


def _single_operation(media: dict[str, Any]) -> CompiledOperation:
    return CompiledOperation(
        operation_type=str(media["media_type"]),
        payload=dict(media.get("payload") or {}),
        media=[media],
    )


def _split_media(media: list[dict[str, Any]]) -> list[CompiledOperation]:
    operations: list[CompiledOperation] = []
    cursor = 0
    while cursor < len(media):
        family = _group_family(str(media[cursor]["media_type"]))
        end = cursor + 1
        while (
            family is not None and end < len(media) and _group_family(str(media[end]["media_type"])) == family
        ):
            end += 1
        run = media[cursor:end]
        if family is None or len(run) == 1:
            operations.extend(_single_operation(item) for item in run)
        else:
            for offset in range(0, len(run), 10):
                chunk = run[offset : offset + 10]
                if len(chunk) == 1:
                    operations.append(_single_operation(chunk[0]))
                else:
                    operations.append(CompiledOperation("media_group", {"family": family}, chunk))
        cursor = end
    return operations


def _attach_keyboard(
    operations: list[CompiledOperation], keyboard: dict[str, Any]
) -> list[CompiledOperation]:
    if not operations:
        return operations
    last = operations[-1]
    if last.operation_type == "media_group":
        detached = last.media[-1]
        remaining = last.media[:-1]
        if len(remaining) == 1:
            operations[-1] = _single_operation(remaining[0])
        else:
            operations[-1] = CompiledOperation(last.operation_type, last.payload, remaining)
        operations.append(_single_operation(detached))
        last = operations[-1]
    operations[-1] = CompiledOperation(
        last.operation_type,
        {**last.payload, "keyboard": keyboard},
        last.media,
    )
    return operations


def compile_step(
    step_payload: dict[str, Any],
    attachments: list[AttachmentSpec],
    keyboard: dict[str, Any] | None = None,
) -> list[CompiledOperation]:
    """Compile one logical editor step into legal, independently retryable Bot API calls."""
    if not attachments:
        operation_type = str(step_payload.get("type") or "text")
        operations = [CompiledOperation(operation_type, dict(step_payload), [])]
    else:
        item_payloads = _payloads_for_attachments(step_payload, attachments)
        media = [attachment.as_media(item_payloads[index]) for index, attachment in enumerate(attachments)]
        operations = _split_media(media)
    return _attach_keyboard(operations, keyboard) if keyboard else operations
