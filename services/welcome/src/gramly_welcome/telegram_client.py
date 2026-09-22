from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Any

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError, TelegramServerError

from .metrics import TELEGRAM_CLIENT_CACHE
from .resilience import telegram_circuit


@dataclass
class _Entry:
    bot: Bot
    last_used: float
    in_use: int = 0


class TelegramClientPool:
    """Bounded pool of long-lived Telegram clients.

    Aiogram's AIOHTTP session owns the TCP connection pool. Reusing it removes a
    TLS/DNS handshake from every delivery while keeping raw bot tokens out of
    cache keys and diagnostics.
    """

    def __init__(self, *, max_clients: int = 256, timeout_seconds: int = 30) -> None:
        self.max_clients = max_clients
        self.timeout_seconds = timeout_seconds
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def acquire(self, token: str) -> tuple[str, Bot]:
        key = self._key(token)
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                TELEGRAM_CLIENT_CACHE.labels("hit").inc()
                entry.last_used = monotonic()
                entry.in_use += 1
                self._entries.move_to_end(key)
                return key, entry.bot
            bot = Bot(token=token, session=AiohttpSession(timeout=self.timeout_seconds))
            TELEGRAM_CLIENT_CACHE.labels("miss").inc()
            self._entries[key] = _Entry(bot=bot, last_used=monotonic(), in_use=1)
            await self._evict_unlocked()
            return key, bot

    async def release(self, key: str) -> None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry.in_use = max(0, entry.in_use - 1)
                entry.last_used = monotonic()
            await self._evict_unlocked()

    async def _evict_unlocked(self) -> None:
        while len(self._entries) > self.max_clients:
            idle_key = next((key for key, entry in self._entries.items() if not entry.in_use), None)
            if idle_key is None:
                return
            entry = self._entries.pop(idle_key)
            await entry.bot.session.close()

    async def close(self) -> None:
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        await asyncio.gather(*(entry.bot.session.close() for entry in entries), return_exceptions=True)


telegram_clients = TelegramClientPool()


@asynccontextmanager
async def bot_client(token: str, *, bot_factory: Any = Bot) -> AsyncIterator[Bot]:
    # The pool, rather than each operation, owns the session lifecycle.
    probe = await telegram_circuit.enter()
    try:
        if bot_factory is Bot:
            key, bot = await telegram_clients.acquire(token)
            try:
                yield bot
            finally:
                await telegram_clients.release(key)
        else:
            # Keep dependency injection used by tests and local adapters.
            async with bot_factory(token=token, session=AiohttpSession(timeout=30)) as bot:
                yield bot
    except (TelegramNetworkError, TelegramServerError, OSError):
        await telegram_circuit.failure(probe)
        raise
    except Exception:
        # 400/401/403/429 describe a particular request or bot and must not
        # take healthy bots out through a process-wide dependency breaker.
        await telegram_circuit.success(probe)
        raise
    else:
        await telegram_circuit.success(probe)
