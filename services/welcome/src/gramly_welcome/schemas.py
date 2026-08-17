from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class TelegramUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    update_id: int

    @field_validator("update_id")
    @classmethod
    def non_negative_update_id(cls, value: int) -> int:
        if value < 0:
            raise ValueError("update_id must be non-negative")
        return value

    def as_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class AcceptedResponse(BaseModel):
    ok: bool = True
    duplicate: bool = False
