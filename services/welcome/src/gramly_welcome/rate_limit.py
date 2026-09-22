from __future__ import annotations

from collections.abc import Awaitable
from time import monotonic
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError


class RateLimitUnavailable(RuntimeError):
    pass


_ACQUIRE = """
local tm = redis.call('TIME')
local now = tonumber(tm[1]) * 1000000 + tonumber(tm[2])
local blocked = tonumber(redis.call('GET', KEYS[4]) or '0')
if blocked > now then return {0, math.ceil((blocked - now) / 1000)} end
local function inspect(key, interval, burst)
  local tat = tonumber(redis.call('GET', key) or '0')
  local base = math.max(tat, now)
  local allowed_at = tat - (burst * interval)
  return now >= allowed_at, base + interval, math.max(0, allowed_at - now)
end
local bot_ok, bot_tat, bot_wait = inspect(KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]))
local chat_ok, chat_tat, chat_wait = inspect(KEYS[2], tonumber(ARGV[3]), tonumber(ARGV[4]))
local bulk_ok, bulk_tat, bulk_wait = true, 0, 0
if ARGV[7] == 'bulk' then
  bulk_ok, bulk_tat, bulk_wait = inspect(KEYS[3], tonumber(ARGV[5]), tonumber(ARGV[6]))
end
if not bot_ok or not chat_ok or not bulk_ok then
  return {0, math.ceil(math.max(bot_wait, chat_wait, bulk_wait) / 1000)}
end
redis.call('SET', KEYS[1], bot_tat, 'PX', 3000)
redis.call('SET', KEYS[2], chat_tat, 'PX', 3000)
if ARGV[7] == 'bulk' then redis.call('SET', KEYS[3], bulk_tat, 'PX', 3000) end
return {1, 0}
"""

_COOLDOWN = """
local tm = redis.call('TIME')
local until_us = (tonumber(tm[1]) * 1000000 + tonumber(tm[2])) + tonumber(ARGV[1]) * 1000000
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if until_us > current then
  redis.call('SET', KEYS[1], until_us, 'PX', tonumber(ARGV[1]) * 1000 + 1000)
end
return until_us
"""


class TelegramRateLimiter:
    def __init__(
        self,
        redis: Redis,
        *,
        bot_limit: int,
        chat_limit: int,
        urgent_reserve: int = 8,
    ) -> None:
        self.redis = redis
        self.bot_limit = bot_limit
        self.chat_limit = chat_limit
        self.bulk_limit = max(1, bot_limit - urgent_reserve)
        self._consecutive_failures = 0
        self._open_until = 0.0

    async def acquire_wait(
        self, bot_id: int, chat_id: int, *, lane: str = "standard"
    ) -> int | None:
        if monotonic() < self._open_until:
            raise RateLimitUnavailable("Valkey circuit open")
        try:
            result = await cast(
                Awaitable[Any],
                self.redis.eval(
                    _ACQUIRE,
                    4,
                    f"welcome:gcra:bot:{bot_id}",
                    f"welcome:gcra:chat:{bot_id}:{chat_id}",
                    f"welcome:gcra:bulk:{bot_id}",
                    f"welcome:cooldown:bot:{bot_id}",
                    str(1_000_000 / self.bot_limit),
                    str(self.bot_limit - 1),
                    str(1_000_000 / self.chat_limit),
                    str(max(0, self.chat_limit - 1)),
                    str(1_000_000 / self.bulk_limit),
                    str(self.bulk_limit - 1),
                    lane,
                ),
            )
        except RedisError as exc:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._open_until = monotonic() + 5
            raise RateLimitUnavailable("Valkey unavailable") from exc
        self._consecutive_failures = 0
        allowed, wait_ms = int(result[0]), int(result[1])
        return None if allowed else max(1, wait_ms)

    async def acquire(self, bot_id: int, chat_id: int, *, lane: str = "standard") -> bool:
        return await self.acquire_wait(bot_id, chat_id, lane=lane) is None

    async def block_bot(self, bot_id: int, retry_after_seconds: int) -> None:
        try:
            await cast(
                Awaitable[Any],
                self.redis.eval(
                    _COOLDOWN,
                    1,
                    f"welcome:cooldown:bot:{bot_id}",
                    str(max(1, retry_after_seconds)),
                ),
            )
        except RedisError as exc:
            raise RateLimitUnavailable("Valkey unavailable") from exc
