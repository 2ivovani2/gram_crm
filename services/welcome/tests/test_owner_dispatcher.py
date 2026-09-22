from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    AnswerCallbackQuery,
    AnswerPreCheckoutQuery,
    DeleteMessage,
    EditMessageText,
    SendMessage,
)
from aiogram.methods.base import TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
    SuccessfulPayment,
    Update,
    User,
)

from gramly_welcome import owner_bot
from gramly_welcome.bot_ui import BotUiTheme
from gramly_welcome.content_compiler import CompiledOperation
from gramly_welcome.models import ContentFlow, ContentFlowVersion, ContentStep, ManagedBot, Owner
from gramly_welcome.owner_ui import OwnerUiMiddleware


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, **_kwargs: Any) -> None:
        self.values[key] = value


class TestStorage(MemoryStorage):
    __test__ = False

    def __init__(self) -> None:
        super().__init__()
        self.redis = FakeRedis()


class TelegramSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.messages: dict[int, Message] = {}
        self.next_id = 40

    async def close(self) -> None:
        pass

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            if isinstance(method, SendMessage):
                self.next_id += 1
                message_id = self.next_id
            else:
                message_id = int(method.message_id or 0)
            message = Message(
                message_id=message_id,
                date=datetime.now(UTC),
                chat=Chat(id=11, type="private"),
                from_user=User(id=bot.id, is_bot=True, first_name="Gramly"),
                text=method.text,
                reply_markup=method.reply_markup
                if isinstance(method.reply_markup, InlineKeyboardMarkup)
                else None,
            ).as_(bot)
            self.messages[message_id] = message
            return message
        if isinstance(method, (AnswerCallbackQuery, AnswerPreCheckoutQuery, DeleteMessage)):
            if isinstance(method, DeleteMessage):
                self.messages.pop(method.message_id, None)
            return True
        raise AssertionError(f"Unexpected Telegram method: {type(method).__name__}")

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        raise AssertionError("No download in dispatcher smoke")
        yield b""  # pragma: no cover


@pytest.mark.asyncio
async def test_real_owner_dispatcher_routes_encoded_callbacks_and_preserves_fsm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = Owner(id=1, telegram_id=11, first_name="Owner")
    managed = ManagedBot(id=7, owner_id=1, telegram_id=77, username="test_bot", display_name="Test")
    flow = ContentFlow(id=8, bot_id=7, kind="welcome", assignment_mode="all")
    version = ContentFlowVersion(id=9, flow_id=8, version=1, first_delay_seconds=0)
    step = ContentStep(
        id=10, version_id=9, position=0, payload={"type": "text", "text": "Preview"}, delay_after_seconds=10
    )
    final_step = ContentStep(
        id=12, version_id=9, position=1, payload={"type": "text", "text": "Final"}, delay_after_seconds=0
    )
    snapshot = SimpleNamespace(flow=flow, version=version, steps=[step, final_step])

    class Database:
        async def __aenter__(self) -> Database:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def get(self, *args: Any) -> Any:
            return managed

        async def scalars(self, *args: Any) -> Any:
            return SimpleNamespace(all=lambda: [])

        async def scalar(self, *args: Any) -> Any:
            return SimpleNamespace(original_amount=100)

    monkeypatch.setattr(owner_bot, "session_factory", Database)
    monkeypatch.setattr(owner_bot, "owner_from_telegram", AsyncMock(return_value=owner))
    monkeypatch.setattr(
        owner_bot, "check_required_membership", AsyncMock(return_value=SimpleNamespace(allowed=True))
    )
    monkeypatch.setattr(owner_bot, "schedule_learning_notifications", AsyncMock())
    monkeypatch.setattr(owner_bot, "load_bot_ui_theme", AsyncMock(return_value=BotUiTheme()))
    monkeypatch.setattr(owner_bot, "list_owned_bots", AsyncMock(return_value=([managed], 1)))
    monkeypatch.setattr(owner_bot, "_get_owned", AsyncMock(return_value=managed))
    monkeypatch.setattr(owner_bot, "bot_statistics", AsyncMock(return_value={"live": 1, "dead": 0}))
    monkeypatch.setattr(owner_bot, "bot_channels", AsyncMock(return_value=[]))
    monkeypatch.setattr(owner_bot, "ensure_default_welcome_flow", AsyncMock(return_value=flow))
    monkeypatch.setattr(owner_bot, "open_draft", AsyncMock(return_value=version))
    monkeypatch.setattr(owner_bot, "draft_snapshot", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(
        owner_bot,
        "compile_preview_operations",
        AsyncMock(return_value=[CompiledOperation("text", step.payload, [])]),
    )
    monkeypatch.setattr(owner_bot, "ObjectStorage", lambda settings: object())
    mutation = AsyncMock()
    monkeypatch.setattr(owner_bot, "set_step_delay", mutation)
    monkeypatch.setattr(owner_bot, "settle_stars_payment", AsyncMock())

    storage, telegram = TestStorage(), TelegramSession()
    client = Bot("100:TEST", session=telegram, default=DefaultBotProperties(parse_mode="HTML"))
    dispatcher = Dispatcher(storage=storage)
    router = Router()
    # Copy the production registrations, including StateFilter and F.data
    # predicates, without attaching/reparenting the module-global router.
    router.message.handlers.extend(owner_bot.router.message.handlers)
    router.callback_query.handlers.extend(owner_bot.router.callback_query.handlers)
    router.pre_checkout_query.handlers.extend(owner_bot.router.pre_checkout_query.handlers)
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.outer_middleware(OwnerUiMiddleware())
        observer.outer_middleware(owner_bot.OwnerMiddleware())
    dispatcher.pre_checkout_query.outer_middleware(owner_bot.OwnerMiddleware())
    dispatcher.include_router(router)
    user = User(id=11, is_bot=False, first_name="Owner")
    update_id = 0

    def state() -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(storage.redis.values["welcome:owner-ui:100:11:11"]))

    async def send_text(text: str) -> None:
        nonlocal update_id
        update_id += 1
        await dispatcher.feed_update(
            client,
            Update(
                update_id=update_id,
                message=Message(
                    message_id=1000 + update_id,
                    date=datetime.now(UTC),
                    chat=Chat(id=11, type="private"),
                    from_user=user,
                    text=text,
                ),
            ),
        )

    async def click(raw: str) -> str:
        nonlocal update_id
        encoded = str(next(token for token, action in state()["actions"].items() if action == raw))
        assert encoded.startswith("ui:")
        update_id += 1
        await dispatcher.feed_update(
            client,
            Update(
                update_id=update_id,
                callback_query=CallbackQuery(
                    id=str(update_id),
                    chat_instance="owner-chat",
                    from_user=user,
                    message=telegram.messages[state()["message_id"]],
                    data=encoded,
                ),
            ),
        )
        return encoded

    await send_text("/start")
    anchor = state()["message_id"]
    await click("menu:bots")
    await click("bot:7")
    await click("msg:7")
    await click("chain:step:9:10")
    assert state()["preview_ids"]
    await click("chain:delay-input:9:10")
    fsm = dispatcher.fsm.get_context(client, chat_id=11, user_id=11)
    assert await fsm.get_state() == owner_bot.ChainDelayState.waiting_for_delay.state
    assert await fsm.get_data() == {"version_id": 9, "step_id": 10}
    assert state()["preview_ids"] == []
    await send_text("not a delay")
    assert await fsm.get_state() == owner_bot.ChainDelayState.waiting_for_delay.state
    assert not mutation.called
    await click("chain:editor:9")
    assert await fsm.get_state() is None
    await click("chain:step:9:10")
    await click("chain:delay-input:9:10")
    await send_text("01:30")
    assert mutation.await_args is not None
    assert mutation.await_args.args[1:] == (1, 10, 90)
    assert await fsm.get_state() is None
    assert state()["message_id"] == anchor

    # A successful payment remains an independent notification, not a new UI
    # anchor, and doesn't erase another in-progress owner form.
    await click("chain:step:9:10")
    await click("chain:delay-input:9:10")
    prior_state = await fsm.get_data()
    update_id += 1
    await dispatcher.feed_update(
        client,
        Update(
            update_id=update_id,
            pre_checkout_query=PreCheckoutQuery(
                id="pre-checkout",
                from_user=user,
                currency="XTR",
                total_amount=100,
                invoice_payload="11111111-1111-1111-1111-111111111111",
            ),
        ),
    )
    assert any(isinstance(call, AnswerPreCheckoutQuery) and call.ok for call in telegram.calls)
    assert await fsm.get_state() == owner_bot.ChainDelayState.waiting_for_delay.state
    # A confirmed payment must be settled even if channel membership changed
    # after checkout. Membership still guards ordinary owner navigation.
    membership = AsyncMock(return_value=SimpleNamespace(allowed=False))
    monkeypatch.setattr(owner_bot, "check_required_membership", membership)
    update_id += 1
    await dispatcher.feed_update(
        client,
        Update(
            update_id=update_id,
            message=Message(
                message_id=3000,
                date=datetime.now(UTC),
                chat=Chat(id=11, type="private"),
                from_user=user,
                successful_payment=SuccessfulPayment(
                    currency="XTR",
                    total_amount=100,
                    invoice_payload="invoice",
                    telegram_payment_charge_id="charge",
                    provider_payment_charge_id="provider",
                ),
            ),
        ),
    )
    assert state()["message_id"] == anchor
    assert await fsm.get_data() == prior_state
    assert not membership.called
    assert any(
        isinstance(call, SendMessage) and "Business активирован" in call.text for call in telegram.calls
    )
    assert all(call.message_id == anchor for call in telegram.calls if isinstance(call, EditMessageText))
    monkeypatch.setattr(
        owner_bot, "check_required_membership", AsyncMock(return_value=SimpleNamespace(allowed=True))
    )
    await send_text("📈 Аналитика")
    assert await fsm.get_state() is None
    assert "Аналитика GramlyHello" in (telegram.messages[anchor].text or "")
    assert state()["route"] == "menu:analytics"

    # The same trusted notification is routed normally with no active form.
    update_id += 1
    await dispatcher.feed_update(
        client,
        Update(
            update_id=update_id,
            message=Message(
                message_id=3001,
                date=datetime.now(UTC),
                chat=Chat(id=11, type="private"),
                from_user=user,
                successful_payment=SuccessfulPayment(
                    currency="XTR",
                    total_amount=100,
                    invoice_payload="second-invoice",
                    telegram_payment_charge_id="second-charge",
                    provider_payment_charge_id="provider",
                ),
            ),
        ),
    )
    assert state()["message_id"] == anchor
    assert (
        sum(isinstance(call, SendMessage) and "Business активирован" in call.text for call in telegram.calls)
        == 2
    )
    await send_text("неизвестный текст")
    assert "Выберите действие" in (telegram.messages[anchor].text or "")
    assert state()["message_id"] == anchor
    await send_text("/start")
    new_anchor = state()["message_id"]
    assert new_anchor != anchor
    assert anchor not in telegram.messages
    assert "добро пожаловать" in (telegram.messages[new_anchor].text or "")
    assert await fsm.get_state() is None
    await client.session.close()
