from __future__ import annotations

import time

import httpx
import pytest
from anyio import EndOfStream

from ops.welcome.load_test import Results, fire, percentile


def test_percentile_uses_nearest_rank() -> None:
    values = [0.1, 0.4, 0.2, 0.3]

    assert percentile(values, 0.50) == pytest.approx(0.2)
    assert percentile(values, 0.95) == pytest.approx(0.4)


def test_percentile_of_empty_sample_fails_closed() -> None:
    assert percentile([], 0.95) == pytest.approx(float("inf"))


@pytest.mark.asyncio
async def test_transport_exception_is_counted_without_killing_worker() -> None:
    async def fail_transport(_request: httpx.Request) -> httpx.Response:
        raise EndOfStream

    results = Results()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(fail_transport)
    ) as client:
        await fire(
            client, "https://example.test", "secret", 1, time.perf_counter(), results
        )

    assert results.accepted == 0
    assert results.failed == 1
    assert len(results.latencies) == 1
