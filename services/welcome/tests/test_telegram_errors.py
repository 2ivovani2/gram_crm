from gramly_welcome.telegram_errors import normalize_bad_request


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
