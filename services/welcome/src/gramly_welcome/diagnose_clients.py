"""Read-only token checks by default; --apply records deduplicated incidents.

This does not change webhooks, rights, auto-approval, tokens or historical jobs.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from sqlalchemy import select

from .bot_diagnostics import record_incident, resolve_incident
from .config import get_settings
from .crypto import TokenDecryptionError, TokenKeyring
from .db import session_factory
from .models import ManagedBot


async def diagnose(*, apply: bool, bot_id: int | None) -> None:
    async with session_factory() as session:
        query = select(ManagedBot).where(ManagedBot.is_active.is_(True))
        if bot_id is not None:
            query = query.where(ManagedBot.id == bot_id)
        bots = (await session.scalars(query.order_by(ManagedBot.id))).all()
    keyring = TokenKeyring.parse(get_settings().token_encryption_keys)
    for managed in bots:
        try:
            token = keyring.decrypt(managed.token_ciphertext)
            async with Bot(token, session=AiohttpSession(timeout=10)) as bot:
                me = await bot.get_me()
            result = "ok" if me.id == managed.telegram_id else "token_identity_mismatch"
        except TelegramUnauthorizedError:
            result = "invalid_bot_token"
        except (TelegramAPIError, OSError):
            result = "telegram_temporary_error"
        except (TokenDecryptionError, ValueError):
            result = "token_decryption_error"
        if apply:
            async with session_factory() as session, session.begin():
                if result == "invalid_bot_token":
                    await record_incident(session, managed.id, result)
                elif result == "ok":
                    await resolve_incident(session, managed.id, "invalid_bot_token")
        print(json.dumps({"bot_id": managed.id, "result": result, "apply": apply}), flush=True)


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--bot-id", type=int)
    args = parser.parse_args()
    asyncio.run(diagnose(apply=args.apply, bot_id=args.bot_id))
