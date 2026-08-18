#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import math
import os
import time
from dataclasses import dataclass, field

import asyncpg  # type: ignore[import-untyped]
import httpx


@dataclass
class Results:
    latencies: list[float] = field(default_factory=list)
    schedule_lags: list[float] = field(default_factory=list)
    accepted: int = 0
    failed: int = 0


@dataclass(frozen=True)
class DatabaseState:
    persisted: int
    completed: int


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
    scheduled_at: float,
    results: Results,
) -> None:
    results.schedule_lags.append(max(0.0, time.perf_counter() - scheduled_at))
    started = time.perf_counter()
    try:
        response = await client.post(
            url,
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
            json={
                "update_id": update_id,
                # A valid Telegram update that the event worker can acknowledge
                # without creating contacts or scheduling outbound deliveries.
                "poll": {
                    "id": f"gramly-load-{update_id}",
                    "question": "staging-load",
                    "options": [],
                },
            },
        )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if (
            response.status_code == 200
            and isinstance(payload, dict)
            and payload.get("ok") is True
        ):
            results.accepted += 1
        else:
            results.failed += 1
    except httpx.HTTPError:
        results.failed += 1
    finally:
        results.latencies.append(time.perf_counter() - started)


async def database_state(
    database_url: str,
    first_id: int,
    total: int,
    source_key: str,
) -> DatabaseState:
    connection = await asyncpg.connect(database_url)
    try:
        row = await connection.fetchrow(
            "SELECT count(*) AS persisted, "
            "count(*) FILTER (WHERE status = 'completed') AS completed "
            "FROM inbox_event "
            "WHERE source_key = $1 AND update_id >= $2 AND update_id < $3",
            source_key,
            first_id,
            first_id + total,
        )
        assert row is not None
        return DatabaseState(
            persisted=int(row["persisted"]), completed=int(row["completed"])
        )
    finally:
        await connection.close()


async def wait_for_database(
    database_url: str,
    first_id: int,
    total: int,
    source_key: str,
    expected: int,
    timeout: float,
) -> DatabaseState:
    deadline = time.monotonic() + timeout
    state = DatabaseState(persisted=0, completed=0)
    while time.monotonic() < deadline:
        state = await database_state(database_url, first_id, total, source_key)
        if state.persisted == expected and state.completed == expected:
            return state
        await asyncio.sleep(0.5)
    return state


async def cleanup_database(
    database_url: str, first_id: int, total: int, source_key: str
) -> int:
    connection = await asyncpg.connect(database_url)
    try:
        result = await connection.execute(
            "DELETE FROM inbox_event "
            "WHERE source_key = $1 AND update_id >= $2 AND update_id < $3",
            source_key,
            first_id,
            first_id + total,
        )
        return int(result.rsplit(" ", 1)[-1])
    finally:
        await connection.close()


async def run(args: argparse.Namespace) -> int:
    results = Results()
    first_id = int(time.time() * 1_000_000)
    total = args.rate * args.seconds
    timeout = httpx.Timeout(args.timeout)
    queue: asyncio.Queue[tuple[int, float] | None] = asyncio.Queue(
        maxsize=args.concurrency * 2
    )
    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        started = time.perf_counter()

        async def worker() -> None:
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    update_id, scheduled_at = item
                    await fire(
                        client,
                        args.url,
                        args.secret,
                        update_id,
                        scheduled_at,
                        results,
                    )
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(args.concurrency)]
        for index in range(total):
            target = started + index / args.rate
            await asyncio.sleep(max(0, target - time.perf_counter()))
            await queue.put((first_id + index, target))
        for _ in workers:
            await queue.put(None)
        await queue.join()
        await asyncio.gather(*workers)
        duration = time.perf_counter() - started

    p95 = percentile(results.latencies, 0.95)
    p99 = percentile(results.latencies, 0.99)
    schedule_p95 = percentile(results.schedule_lags, 0.95)
    state = DatabaseState(persisted=results.accepted, completed=results.accepted)
    if args.database_url:
        state = await wait_for_database(
            args.database_url,
            first_id,
            total,
            args.source_key,
            results.accepted,
            args.drain_timeout,
        )
    cleaned = 0
    if args.cleanup:
        cleaned = await cleanup_database(
            args.database_url, first_id, total, args.source_key
        )
    print(
        f"sent={total} accepted={results.accepted} failed={results.failed} "
        f"persisted={state.persisted} completed={state.completed} "
        f"p95={p95:.3f}s p99={p99:.3f}s schedule_p95={schedule_p95:.3f}s "
        f"duration={duration:.1f}s rate={total / duration:.1f}/s cleanup={cleaned}"
    )
    passed = (
        results.failed == 0
        and results.accepted == total
        and state.persisted == total
        and state.completed == total
        and p95 <= args.max_p95
        and p99 <= args.max_p99
        and schedule_p95 <= args.max_schedule_p95
        and duration <= args.seconds + args.max_overrun
    )
    return 0 if passed else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bounded Gramly Welcome webhook load gate"
    )
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--secret",
        default=os.environ.get("WELCOME_LOAD_TEST_SECRET", ""),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--rate", type=int, default=100)
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--concurrency", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-p95", type=float, default=0.250)
    parser.add_argument("--max-p99", type=float, default=1.0)
    parser.add_argument("--max-schedule-p95", type=float, default=0.100)
    parser.add_argument("--max-overrun", type=float, default=5.0)
    parser.add_argument("--drain-timeout", type=float, default=180.0)
    parser.add_argument(
        "--database-url", default=os.environ.get("WELCOME_LOAD_TEST_DATABASE_URL", "")
    )
    parser.add_argument("--source-key", default="interface")
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="delete only this run's inbox range after verification",
    )
    args = parser.parse_args()
    if not args.secret:
        parser.error("set WELCOME_LOAD_TEST_SECRET or pass --secret")
    if args.rate <= 0 or args.seconds <= 0 or args.concurrency <= 0:
        parser.error("rate, seconds and concurrency must be positive")
    if args.cleanup and not args.database_url:
        parser.error(
            "--cleanup requires WELCOME_LOAD_TEST_DATABASE_URL or --database-url"
        )
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
