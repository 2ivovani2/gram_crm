from __future__ import annotations

import time
from collections.abc import Awaitable
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError


class RateLimitUnavailable(RuntimeError):
    pass


_ACQUIRE = """
local bot_count = redis.call('INCR', KEYS[1])
if bot_count == 1 then redis.call('EXPIRE', KEYS[1], 2) end
local chat_count = redis.call('INCR', KEYS[2])
if chat_count == 1 then redis.call('EXPIRE', KEYS[2], 2) end
if bot_count > tonumber(ARGV[1]) or chat_count > tonumber(ARGV[2]) then
  return 0
end
return 1
"""


class TelegramRateLimiter:
    def __init__(self, redis: Redis, *, bot_limit: int, chat_limit: int) -> None:
        self.redis = redis
        self.bot_limit = bot_limit
        self.chat_limit = chat_limit

    async def acquire(self, bot_id: int, chat_id: int) -> bool:
        bucket = int(time.time())
        try:
            result = await cast(
                Awaitable[Any],
                self.redis.eval(
                    _ACQUIRE,
                    2,
                    f"welcome:rate:bot:{bot_id}:{bucket}",
                    f"welcome:rate:chat:{bot_id}:{chat_id}:{bucket}",
                    str(self.bot_limit),
                    str(self.chat_limit),
                ),
            )
        except RedisError as exc:
            raise RateLimitUnavailable("Valkey unavailable") from exc
        return bool(result)
