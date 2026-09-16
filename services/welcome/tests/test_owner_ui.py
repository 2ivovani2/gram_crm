from __future__ import annotations

import json
from datetime import UTC, datetime
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import EditMessageText
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    SuccessfulPayment,
    TelegramObject,
    User,
)

from gramly_welcome import owner_ui
from gramly_welcome.owner_ui import (
    OwnerUiMiddleware,
    OwnerUiSession,
    _legacy_navigation,
    _navigation,
    render_owner_ui,
    screen_pages,
)


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, **_kwargs: object) -> None:
        self.values[key] = value


def bot() -> Any:
    return SimpleNamespace(
        id=100,
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)),
        edit_message_text=AsyncMock(),
        delete_message=AsyncMock(),
    )


def keyboard(action: str = "menu:bots") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Боты", callback_data=action)]])


@pytest.mark.asyncio
async def test_restored_screen_removes_legacy_unicode_icons() -> None:
    client = bot()
    session = OwnerUiSession(client, MemoryRedis(), 11, 11)
    session.data.update(message_id=42, pages=["Меню"], markup={"inline_keyboard": [[
        {"text": "↩️ Назад", "callback_data": "menu:bots", "icon_custom_emoji_id": "5974453749601537448"},
    ]]})
    await session.display_page(0)
    button = client.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Назад"
    assert button.icon_custom_emoji_id == "5974453749601537448"


@pytest.mark.asyncio
async def test_anchor_survives_fsm_reset_and_new_worker() -> None:
    client, redis = bot(), MemoryRedis()
    first = OwnerUiSession(client, redis, 11, 11)
    await first.load()
    await first.render("Главное меню", keyboard())
    second = OwnerUiSession(client, redis, 11, 11)
    await second.load()
    await second.render("Мои боты", keyboard("bot:7"))
    assert client.send_message.await_count == 1
    assert client.edit_message_text.call_args.kwargs["message_id"] == 42
    assert second.data["message_id"] == 42
    assert next(iter(first.data["actions"])) not in second.data["actions"]


@pytest.mark.asyncio
async def test_deleted_anchor_recreated_once_but_network_error_never_sends() -> None:
    client, redis = bot(), MemoryRedis()
    session = OwnerUiSession(client, redis, 11, 11)
    session.data["message_id"] = 42
    method = EditMessageText(text="x")
    client.edit_message_text.side_effect = TelegramBadRequest(
        method=method, message="message to edit not found"
    )
    await session.render("Меню", keyboard())
    assert client.send_message.await_count == 1
    client.edit_message_text.side_effect = TelegramNetworkError(method=method, message="offline")
    with pytest.raises(TelegramNetworkError):
        await session.render("Меню", keyboard())
    assert client.send_message.await_count == 1


@pytest.mark.asyncio
async def test_input_errors_keep_controls_and_preview_cleanup_only_owns_preview() -> None:
    client, redis = bot(), MemoryRedis()
    session = OwnerUiSession(client, redis, 11, 11)
    await session.render("Введите задержку", keyboard("chain:editor:7"))
    await session.render("Некорректная задержка", None)
    assert "chain:editor:7" in session.data["actions"].values()
    await session.track_preview((50, 51, 50, 42))
    await session.cleanup_preview()
    assert [call.args[1] for call in client.delete_message.call_args_list] == [50, 51]
    assert session.data["preview_ids"] == []
    assert client.send_message.await_count == 1


def message() -> Message:
    return Message(
        message_id=42,
        date=datetime.now(UTC),
        chat=Chat(id=11, type="private"),
        from_user=User(id=100, is_bot=True, first_name="Gramly"),
        text="Меню",
    )


@pytest.mark.asyncio
async def test_middleware_decodes_and_consumes_action_before_handler_and_rejects_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, redis = bot(), MemoryRedis()
    session = OwnerUiSession(client, redis, 11, 11)
    await session.render("Автопринятие", keyboard("toggle:7"))
    action = next(iter(session.data["actions"]))
    event = CallbackQuery(
        id="q",
        chat_instance="chat",
        from_user=User(id=11, is_bot=False, first_name="Owner"),
        message=message(),
        data=action,
    )
    answer = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    observed = []

    async def handler(callback: TelegramObject, _data: dict[str, Any]) -> None:
        assert isinstance(callback, CallbackQuery)
        observed.append(callback.data)
        saved = json.loads(await redis.get(session.key) or "{}")
        assert action not in saved["actions"]
        # Simulate committed business mutation with no successful UI response.

    data = {"bot": client, "state": SimpleNamespace(storage=SimpleNamespace(redis=redis))}
    middleware = OwnerUiMiddleware()
    await middleware(handler, event, data)
    await middleware(handler, event, data)
    assert observed == ["toggle:7"]
    assert answer.await_count == 1


@pytest.mark.asyncio
async def test_middleware_context_turns_text_input_reply_into_anchor_edit() -> None:
    client, redis = bot(), MemoryRedis()
    session = OwnerUiSession(client, redis, 11, 11)
    await session.render("Введите задержку", keyboard())
    incoming = message().model_copy(
        update={"message_id": 70, "text": "01:30", "from_user": User(id=11, is_bot=False, first_name="Owner")}
    )

    async def handler(event: TelegramObject, _data: dict[str, Any]) -> None:
        assert isinstance(event, Message)
        await render_owner_ui(event, "Сохранено", reply_markup=keyboard())

    await OwnerUiMiddleware()(
        handler, incoming, {"bot": client, "state": SimpleNamespace(storage=SimpleNamespace(redis=redis))}
    )
    assert client.send_message.await_count == 1
    assert client.edit_message_text.call_args.kwargs["message_id"] == 42


@pytest.mark.asyncio
async def test_start_replaces_old_anchor_only_after_success_then_buttons_keep_it() -> None:
    client, redis = bot(), MemoryRedis()
    session = OwnerUiSession(client, redis, 11, 11)
    await session.render("Старое меню", keyboard())
    client.send_message.return_value = SimpleNamespace(message_id=71)
    incoming = message().model_copy(update={
        "message_id": 70, "text": "/start@gramlyHelloBot ref_example",
        "from_user": User(id=11, is_bot=False, first_name="Owner"),
    })

    async def handler(event: TelegramObject, _data: dict[str, Any]) -> None:
        assert isinstance(event, Message)
        client.delete_message.assert_not_awaited()
        await render_owner_ui(event, "Новое меню", reply_markup=keyboard())

    await OwnerUiMiddleware()(handler, incoming, {
        "bot": client, "state": SimpleNamespace(storage=SimpleNamespace(redis=redis)),
    })
    client.delete_message.assert_awaited_once_with(11, 42)
    assert client.send_message.await_count == 2
    latest = OwnerUiSession(client, redis, 11, 11)
    await latest.load()
    assert latest.data["message_id"] == 71
    await latest.render("Боты", keyboard())
    assert client.send_message.await_count == 2
    assert client.edit_message_text.call_args.kwargs["message_id"] == 71


def test_context_back_tracks_navigation_and_settings_parent() -> None:
    session = OwnerUiSession(bot(), MemoryRedis(), 11, 11)
    session.navigate("menu:bots")
    session.navigate("bot:7")
    assert session.back_route == "menu:bots"
    session.navigate("chain:editor:9")
    session.navigate("chain:delay-input:9:first")
    assert session.back_route == "chain:editor:9"
    session.navigate("bot:7", back=True)
    assert session.back_route == "menu:bots"


@pytest.mark.asyncio
async def test_payment_notification_is_separate_and_does_not_require_ui_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = AsyncMock()
    monkeypatch.setattr(owner_ui, "answer_with_ui_fallback", send)
    event = message().model_copy(
        update={
            "successful_payment": SuccessfulPayment(
                currency="XTR",
                total_amount=100,
                invoice_payload="invoice",
                telegram_payment_charge_id="charge",
                provider_payment_charge_id="provider",
            )
        }
    )

    async def handler(value: TelegramObject, _data: dict[str, Any]) -> None:
        assert isinstance(value, Message)
        await render_owner_ui(value, "Оплата получена", reply_markup=keyboard())

    await OwnerUiMiddleware()(handler, event, {})
    assert send.await_count == 1


class TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        assert self.stack.pop() == tag


def test_long_screen_paginates_all_text_preserving_nested_html_and_unicode() -> None:
    content = "<b>Статистика &amp; отчёты <i>" + ("Факт 🚀 " * 1100) + "</i></b>"
    pages = screen_pages(content)
    assert len(pages) > 2
    combined = []
    for page in pages:
        parser = TextOnly()
        parser.feed(page)
        assert parser.stack == []
        value = "".join(parser.parts)
        assert len(value.encode("utf-16-le")) // 2 <= 3500
        combined.append(value)
    assert "".join(combined) == "Статистика & отчёты " + "Факт 🚀 " * 1100


def test_pagination_does_not_split_premium_emoji_at_boundary() -> None:
    content = "я" * 3499 + '<tg-emoji emoji-id="123">🚀</tg-emoji>'
    pages = screen_pages(content)
    assert len(pages) == 2
    assert "<tg-emoji" not in pages[0]
    assert '<tg-emoji emoji-id="123">🚀</tg-emoji>' in pages[1]


@pytest.mark.asyncio
async def test_page_switch_updates_anchor_without_rerunning_business_handler() -> None:
    client, redis = bot(), MemoryRedis()
    session = OwnerUiSession(client, redis, 11, 11)
    await session.render("Статистика " * 1000, keyboard("toggle-approval:7"))
    assert "owner-ui-page:1" in session.data["actions"].values()
    await session.display_page(1)
    assert client.send_message.await_count == 1
    assert client.edit_message_text.call_args.kwargs["message_id"] == 42
    assert "owner-ui-page:0" in session.data["actions"].values()
    assert "toggle-approval:7" in session.data["actions"].values()


@pytest.mark.parametrize(
    "raw",
    [
        "toggle-approval:7",
        "rot-priority:7:8",
        "chain:assign-toggle:7:8",
        "chain:assign-all:7",
        "bot-template:7:8",
        "delete-confirm:7",
        "pay:stars",
        "requests:7:toggle",
        "rotation:7:enable",
        "msg:7:publish",
    ],
)
def test_navigation_allowlist_never_accepts_mutation_callbacks(raw: str) -> None:
    assert not _navigation(raw)
    assert not _legacy_navigation(raw)


@pytest.mark.parametrize("raw", ["msg:7", "farewell:7", "guide:99", "chain:editor:7"])
def test_legacy_callbacks_cannot_initialize_drafts_or_advance_guide(raw: str) -> None:
    assert _navigation(raw)
    assert not _legacy_navigation(raw)


@pytest.mark.asyncio
async def test_navigation_state_persists_even_if_handler_has_no_ui_response() -> None:
    client, redis = bot(), MemoryRedis()
    event = CallbackQuery(
        id="q",
        chat_instance="chat",
        from_user=User(id=11, is_bot=False, first_name="Owner"),
        message=message(),
        data="menu:bots",
    )
    data = {"bot": client, "state": SimpleNamespace(storage=SimpleNamespace(redis=redis))}
    handler = AsyncMock()
    await OwnerUiMiddleware()(handler, event, data)
    restored = OwnerUiSession(client, redis, 11, 11)
    await restored.load()
    assert restored.data["route"] == "menu:bots"
    assert restored.data["history"] == ["menu:home"]
    assert handler.await_count == 1
