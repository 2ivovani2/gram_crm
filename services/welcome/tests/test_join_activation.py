from __future__ import annotations

import uuid
from typing import cast

import pytest
from aiogram.types import InlineKeyboardMarkup
from cryptography.fernet import Fernet

from gramly_welcome import telegram_delivery
from gramly_welcome.crypto import TokenKeyring
from gramly_welcome.join_activation import (
    ActivationContext,
    activation_token_digest,
    activation_token_valid,
    build_activation_token,
)
from gramly_welcome.models import Channel, Contact, JoinRequest, JoinRequestActivation, ManagedBot


def test_activation_token_is_stable_and_fits_telegram_start_parameter() -> None:
    public_id = uuid.uuid4()
    token = build_activation_token(public_id, "bot-path-secret")

    assert token == build_activation_token(public_id, "bot-path-secret")
    assert token.startswith(public_id.hex + "_")
    assert len("gramly_" + token) <= 64


def test_activation_token_is_bound_to_bot_and_stored_as_digest() -> None:
    public_id = uuid.uuid4()
    token = build_activation_token(public_id, "correct-secret")
    activation = JoinRequestActivation(
        public_id=public_id,
        token_digest=activation_token_digest(token),
    )
    bot = ManagedBot(path_secret="correct-secret")

    assert activation_token_valid(activation, bot, token)
    bot.path_secret = "another-secret"
    assert not activation_token_valid(activation, bot, token)
    bot.path_secret = "correct-secret"
    assert not activation_token_valid(activation, bot, token[:-1] + "x")


@pytest.mark.asyncio
async def test_activation_message_falls_back_when_web_app_button_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, object]] = []

    class RejectedWebApp(Exception):
        pass

    class FakeBot:
        def __init__(self, *, token: str, session: object = None) -> None:
            assert token == "plain-token"

        async def __aenter__(self) -> FakeBot:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def send_message(self, chat_id: int, text: str, **kwargs: object) -> None:
            sent.append({"chat_id": chat_id, "text": text, **kwargs})
            if len(sent) == 1:
                raise RejectedWebApp

    monkeypatch.setattr(telegram_delivery, "Bot", FakeBot)
    monkeypatch.setattr(telegram_delivery, "TelegramBadRequest", RejectedWebApp)
    keyring = TokenKeyring(keys={1: Fernet(Fernet.generate_key())}, current_version=1)
    context = ActivationContext(
        activation=JoinRequestActivation(public_id=uuid.uuid4()),
        request=JoinRequest(user_chat_id=9988),
        bot=ManagedBot(
            public_id=uuid.uuid4(),
            path_secret="path-secret",
            username="customer_bot",
            token_ciphertext=keyring.encrypt("plain-token"),
        ),
        channel=Channel(title="Тестовый канал"),
        contact=Contact(telegram_id=42),
    )

    await telegram_delivery.send_join_activation(
        context, keyring, activation_base_url="https://hello.gramly.tech/activate"
    )

    assert len(sent) == 2
    fallback_markup = cast(InlineKeyboardMarkup, sent[1]["reply_markup"])
    button = fallback_markup.inline_keyboard[0][0]
    assert button.web_app is None
    assert button.url is not None and button.url.startswith("https://t.me/customer_bot?start=gramly_")
