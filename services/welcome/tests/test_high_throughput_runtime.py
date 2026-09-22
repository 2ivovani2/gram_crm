from __future__ import annotations

from typing import Any

import pytest

from gramly_welcome.delivery_streams import decode_stream_fields, stream_name
from gramly_welcome.rate_limit import TelegramRateLimiter
from gramly_welcome.resilience import CircuitBreaker, CircuitOpen


class FakeRedis:
    def __init__(self, result: list[int]) -> None:
        self.result = result
        self.calls: list[tuple[Any, ...]] = []

    async def eval(self, *args: Any) -> list[int]:
        self.calls.append(args)
        return self.result


async def test_gcra_uses_hierarchical_bulk_bucket_and_urgent_reserve() -> None:
    redis = FakeRedis([1, 0])
    limiter = TelegramRateLimiter(redis, bot_limit=28, chat_limit=1, urgent_reserve=8)  # type: ignore[arg-type]

    assert await limiter.acquire(10, 20, lane="bulk")
    call = redis.calls[0]
    assert call[1] == 4
    assert "welcome:gcra:bot:10" in call
    assert "welcome:gcra:chat:10:20" in call
    assert "welcome:gcra:bulk:10" in call
    assert call[-1] == "bulk"
    assert limiter.bulk_limit == 20


async def test_gcra_returns_precise_retry_wait() -> None:
    limiter = TelegramRateLimiter(FakeRedis([0, 375]), bot_limit=28, chat_limit=1)  # type: ignore[arg-type]
    assert await limiter.acquire_wait(1, 2) == 375


async def test_circuit_breaker_opens_and_recovers_through_probes() -> None:
    breaker = CircuitBreaker(
        "test", minimum_requests=2, failure_ratio=0.5, window_seconds=30,
        open_seconds=0, half_open_probes=2,
    )
    await breaker.failure(await breaker.enter())
    await breaker.failure(await breaker.enter())

    first_probe = await breaker.enter()
    second_probe = await breaker.enter()
    assert first_probe and second_probe
    with pytest.raises(CircuitOpen):
        await breaker.enter()
    await breaker.success(first_probe)
    await breaker.success(second_probe)
    assert await breaker.enter() is False


def test_stream_messages_only_expose_job_identity() -> None:
    assert stream_name("welcome:delivery", "urgent") == "welcome:delivery:urgent"
    assert decode_stream_fields({b"job_id": b"42", b"source_kind": b"join_request"}) == (
        42,
        "join_request",
    )
    with pytest.raises(ValueError):
        stream_name("welcome:delivery", "unknown")


def test_delivery_runtime_is_split_into_isolated_lanes() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    runtime = (root / "infra/kubernetes/base/welcome-runtime/workers.yaml").read_text()
    scaling = (root / "infra/kubernetes/base/welcome-autoscaling/workers.yaml").read_text()
    for lane in ("urgent,standard", "interactive", "bulk"):
        assert f'value: "{lane}"' in runtime
    assert "WELCOME_DELIVERY_CONCURRENCY" in runtime
    assert "gramly-welcome-worker-interactive" in scaling
    assert "gramly-welcome-worker-bulk" in scaling
    assert "fallback: {failureThreshold: 3, replicas: 4}" in scaling


def test_delivery_job_migration_dual_writes_every_legacy_queue() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    migration = (
        root / "services/welcome/alembic/versions/0032_delivery_jobs.py"
    ).read_text()
    for source in (
        "delivery_operation",
        "greeting_delivery",
        "join_request",
        "join_request_activation",
        "client_message",
        "broadcast_recipient",
        "rotation_recommendation",
    ):
        assert f'"{source}"' in migration
        assert "trg_{table}_delivery_job" in migration
    assert "FOR UPDATE SKIP LOCKED" not in migration
    assert "PARTITION BY RANGE (finished_at)" in migration


def test_crypto_webhook_persists_before_async_provider_verification() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    api = (root / "services/welcome/src/gramly_welcome/api.py").read_text()
    worker = (root / "services/welcome/src/gramly_welcome/workers/billing.py").read_text()
    webhook = api[api.index("async def crypto_pay_webhook"):api.index("def _secret_valid")]
    assert "register_payment_event" in webhook
    assert "CryptoPayClient" not in webhook
    assert "process_one_payment_event" in worker
    assert "verify_and_settle_crypto_invoice" in worker
