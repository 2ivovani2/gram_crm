import pytest

from gramly_welcome.telegram_errors import delivery_failure_scope, log_delivery_failure, normalize_bad_request


class BadRequest(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def test_normalizes_join_request_terminal_reasons() -> None:
    assert normalize_bad_request(BadRequest("Bad Request: HIDE_REQUESTER_MISSING")) == (
        "request_not_pending"
    )
    assert normalize_bad_request(BadRequest("Bad Request: USER_ALREADY_PARTICIPANT")) == (
        "user_already_participant"
    )
    assert normalize_bad_request(BadRequest("Bad Request: CHAT_ADMIN_REQUIRED")) == (
        "bot_missing_approve_permission"
    )


def test_normalized_error_does_not_leak_dynamic_identifiers() -> None:
    reason = normalize_bad_request(
        BadRequest("Bad Request: invalid user 1091178415 @private_username")
    )
    assert "1091178415" not in reason
    assert "private_username" not in reason


@pytest.mark.parametrize(("message", "reason"), [
    ("Bad Request: can't parse entities: offset 123 @private", "telegram_invalid_entities"),
    ("Bad Request: BUTTON_URL_INVALID", "telegram_invalid_keyboard"),
    ("Bad Request: wrong file identifier/HTTP URL specified", "telegram_invalid_media"),
    ("Bad Request: message text is empty", "telegram_invalid_text"),
    ("Bad Request: caption is too long", "telegram_invalid_text"),
])
def test_outgoing_content_errors_are_not_customer_restrictions(message: str, reason: str) -> None:
    assert normalize_bad_request(BadRequest(message)) == reason
    assert delivery_failure_scope(reason) == "platform"


@pytest.mark.parametrize("reason", ["telegram_bad_request", "TelegramBadRequest", "unexpected_new_code"])
def test_unclassified_failures_are_not_blame_assigned(reason: str) -> None:
    assert delivery_failure_scope(reason) == "unknown"


def test_known_recipient_restrictions_remain_client_scoped() -> None:
    assert delivery_failure_scope("bot_blocked_by_user") == "client"
    assert delivery_failure_scope("invalid_bot_token") == "client"


def test_delivery_evidence_does_not_echo_unknown_payload(caplog: pytest.LogCaptureFixture) -> None:
    log_delivery_failure(operation_id=3, delivery_id=4, method="sendMessage",
                         reason="unrecognized @private secret")
    assert '"operation_id": 3' in caplog.text
    assert '"method": "sendMessage"' in caplog.text
    assert '"scope": "unknown"' in caplog.text
    assert "private" not in caplog.text
    assert "secret" not in caplog.text
