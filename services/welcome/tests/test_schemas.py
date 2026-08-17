import pytest
from pydantic import ValidationError

from gramly_welcome.schemas import TelegramUpdate


def test_update_preserves_unknown_telegram_fields() -> None:
    update = TelegramUpdate.model_validate({"update_id": 10, "future_update": {"value": 1}})
    assert update.as_payload()["future_update"] == {"value": 1}


def test_negative_update_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TelegramUpdate.model_validate({"update_id": -1})
