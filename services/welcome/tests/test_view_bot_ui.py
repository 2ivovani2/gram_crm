from gramly_welcome import view_bot
from gramly_welcome.config import Settings


def test_view_menu_puts_analysis_first_and_keeps_navigation(monkeypatch) -> None:
    monkeypatch.setattr(view_bot, "get_settings", lambda: Settings(interface_bot_username="gramly_hello_bot"))
    rows = view_bot._menu().inline_keyboard
    assert rows[0][0].callback_data == "view:analyze"
    assert {button.callback_data for row in rows for button in row if button.callback_data} >= {
        "view:favorites", "view:collections", "view:method",
    }


def test_view_report_actions_fit_three_compact_rows() -> None:
    rows = view_bot._report_keyboard(-10077, public_username="public_channel").inline_keyboard
    assert [len(row) for row in rows] == [2, 2, 2]
    assert rows[-1][-1].callback_data == "view:menu"
