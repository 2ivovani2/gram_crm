#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import math
import os
import time
from dataclasses import dataclass, field

import asyncpg
import httpx


@dataclass
class Results:
    latencies: list[float] = field(default_factory=list)
    accepted: int = 0
    failed: int = 0


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.inf
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]


async def fire(
    client: httpx.AsyncClient,
    url: str,
    secret: str,
    update_id: int,
    results: Results,
) -> None:
    started = time.perf_counter()
    try:
        response = await client.post(
            url,
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
            json={
                "update_id": update_id,
                "message": {
                    "message_id": update_id,
                    "date": int(time.time()),
                    "chat": {"id": update_id, "type": "private"},
                    "from": {"id": update_id, "is_bot": False, "first_name": "Load"},
                    "text": "/start",
                },
            },
        )
        if response.status_code == 200:
            results.accepted += 1
        else:
            results.failed += 1
    except httpx.HTTPError:
        results.failed += 1
    finally:
        results.latencies.append(time.perf_counter() - started)


async def verify_database(database_url: str, first_id: int, total: int, source_key: str) -> int:
    connection = await asyncpg.connect(database_url)
    try:
        return int(
            await connection.fetchval(
                "SELECT count(*) FROM inbox_event "
                "WHERE source_key = $1 AND update_id >= $2 AND update_id < $3",
                source_key,
                first_id,
                first_id + total,
            )
        )
    finally:
        await connection.close()


async def run(args: argparse.Namespace) -> int:
    results = Results()
    first_id = int(time.time() * 1_000_000)
    total = args.rate * args.seconds
    timeout = httpx.Timeout(args.timeout)

    async with httpx.AsyncClient(timeout=timeout, http2=True) as client:
        started = time.perf_counter()
        pending: set[asyncio.Task[None]] = set()
        for index in range(total):
            target = started + index / args.rate
            await asyncio.sleep(max(0, target - time.perf_counter()))
            pending.add(
                asyncio.create_task(
                    fire(client, args.url, args.secret, first_id + index, results)
                )
            )
            if len(pending) >= args.concurrency:
                _, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
        if pending:
            await asyncio.gather(*pending)

    p95 = percentile(results.latencies, 0.95)
    p99 = percentile(results.latencies, 0.99)
    persisted = results.accepted
    if args.database_url:
        persisted = await verify_database(
            args.database_url, first_id, total, args.source_key
        )
    print(
        f"sent={total} accepted={results.accepted} failed={results.failed} "
        f"persisted={persisted} p95={p95:.3f}s p99={p99:.3f}s"
    )
    passed = (
        results.failed == 0
        and persisted == results.accepted
        and p95 <= args.max_p95
        and p99 <= args.max_p99
    )
    return 0 if passed else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bounded Gramly Welcome webhook load gate")
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--secret", default=os.environ.get("WELCOME_LOAD_TEST_SECRET", ""), help=argparse.SUPPRESS
    )
    parser.add_argument("--rate", type=int, default=100)
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--concurrency", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-p95", type=float, default=0.250)
    parser.add_argument("--max-p99", type=float, default=1.0)
    parser.add_argument("--database-url", default=os.environ.get("WELCOME_LOAD_TEST_DATABASE_URL", ""))
    parser.add_argument("--source-key", default="interface")
    args = parser.parse_args()
    if not args.secret:
        parser.error("set WELCOME_LOAD_TEST_SECRET or pass --secret")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
