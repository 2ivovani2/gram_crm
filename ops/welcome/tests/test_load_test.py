from __future__ import annotations

import pytest

from ops.welcome.load_test import percentile


def test_percentile_uses_nearest_rank() -> None:
    values = [0.1, 0.4, 0.2, 0.3]

    assert percentile(values, 0.50) == pytest.approx(0.2)
    assert percentile(values, 0.95) == pytest.approx(0.4)


def test_percentile_of_empty_sample_fails_closed() -> None:
    assert percentile([], 0.95) == pytest.approx(float("inf"))
