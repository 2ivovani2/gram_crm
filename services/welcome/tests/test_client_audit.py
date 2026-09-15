import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gramly_welcome.audit_clients import inspect_telegram, safe_reason


@pytest.mark.asyncio
async def test_audit_is_read_only_and_omits_secrets() -> None:
    bot = AsyncMock()
    bot.get_me.return_value = SimpleNamespace(id=77)
    bot.get_webhook_info.return_value = SimpleNamespace(
        url="https://example.com/client/public/private-path/", pending_update_count=2,
        last_error_date=None, last_error_message="secret body", allowed_updates=["chat_join_request"],
    )
    bot.get_chat_member.return_value = SimpleNamespace(status="administrator", can_invite_users=True)
    managed = SimpleNamespace(id=1, telegram_id=77, auto_approve=True, public_id="public", path_secret="private-path")
    channel = SimpleNamespace(id=2, telegram_id=-123, can_invite_users=False)
    result = await inspect_telegram(bot, managed, [channel], "https://example.com/client")
    assert result["webhook"]["matches_expected_url"]
    assert result["channels"][0]["can_invite_users"] is True
    assert result["channels"][0]["can_send_welcome_messages"] is None
    assert "private-path" not in str(result)
    assert "secret body" not in str(result)
    assert [call[0] for call in bot.mock_calls] == ["get_me", "get_webhook_info", "get_chat_member"]


@pytest.mark.asyncio
async def test_audit_checks_identity_before_probing_channels() -> None:
    bot = AsyncMock()
    bot.get_me.return_value = SimpleNamespace(id=99)
    result = await inspect_telegram(bot, SimpleNamespace(id=1, telegram_id=77, auto_approve=False), [], "unused")
    assert result["token"] == "token_identity_mismatch"
    bot.get_webhook_info.assert_not_awaited()


def test_legacy_provider_error_is_redacted() -> None:
    assert safe_reason("secret user payload") == "other"
    assert safe_reason("telegram_bad_request") == "telegram_bad_request"


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WELCOME_TEST_DATABASE_URL"), reason="PostgreSQL test database required")
async def test_database_audit_queries_and_no_network_on_undecryptable_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from gramly_welcome import audit_clients
    from gramly_welcome.models import ManagedBot, Owner

    engine = create_async_engine(os.environ["WELCOME_TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    unique = uuid.uuid4().int % 1_000_000_000
    async with sessions() as session, session.begin():
        owner = Owner(telegram_id=-unique)
        session.add(owner)
        await session.flush()
        managed = ManagedBot(owner_id=owner.id, public_id=uuid.uuid4(), telegram_id=-unique,
                             display_name="Test", token_ciphertext="test", webhook_secret="test", path_secret="test")
        session.add(managed)
        await session.flush()
        owner_id, bot_id = owner.id, managed.id
    try:
        monkeypatch.setattr(audit_clients, "session_factory", sessions)
        monkeypatch.setattr(audit_clients, "get_settings", lambda: SimpleNamespace(token_encryption_keys="unused"))
        class Keyring:
            @staticmethod
            def parse(value: str) -> "Keyring":
                return Keyring()

            def decrypt(self, value: str) -> str:
                raise ValueError("do not contact Telegram")

        monkeypatch.setattr(audit_clients, "TokenKeyring", Keyring)
        await audit_clients.audit(hours=24, bot_id=bot_id)
        import json
        output = json.loads(capsys.readouterr().out)
        assert output["bot_id"] == bot_id
        assert output["database"]["inbox"] == []
        assert output["database"]["published_flows"] == []
        assert output["database"]["last_success"] == [[None]]
    finally:
        async with sessions() as session, session.begin():
            await session.execute(delete(ManagedBot).where(ManagedBot.id == bot_id))
            await session.execute(delete(Owner).where(Owner.id == owner_id))
        await engine.dispose()
