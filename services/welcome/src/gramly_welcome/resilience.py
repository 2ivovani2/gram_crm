from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from time import monotonic

from .metrics import CIRCUIT_STATE


class CircuitOpen(OSError):
    pass


@dataclass
class CircuitBreaker:
    name: str
    minimum_requests: int = 20
    failure_ratio: float = 0.5
    window_seconds: float = 30
    open_seconds: float = 15
    half_open_probes: int = 3
    _outcomes: deque[tuple[float, bool]] = field(default_factory=deque)
    _open_until: float = 0
    _probe_in_flight: int = 0
    _probe_successes: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _prune(self, now: float) -> None:
        boundary = now - self.window_seconds
        while self._outcomes and self._outcomes[0][0] < boundary:
            self._outcomes.popleft()

    async def enter(self) -> bool:
        """Return True for a half-open probe and False for a normal request."""
        now = monotonic()
        async with self._lock:
            if self._open_until > now:
                CIRCUIT_STATE.labels(self.name).set(1)
                raise CircuitOpen(f"{self.name} circuit open")
            if self._open_until:
                if self._probe_in_flight >= self.half_open_probes:
                    raise CircuitOpen(f"{self.name} half-open probes exhausted")
                self._probe_in_flight += 1
                return True
            return False

    async def success(self, probe: bool) -> None:
        now = monotonic()
        async with self._lock:
            if probe:
                self._probe_in_flight = max(0, self._probe_in_flight - 1)
                self._probe_successes += 1
                if self._probe_successes >= self.half_open_probes:
                    self._open_until = 0
                    self._probe_successes = 0
                    self._outcomes.clear()
                    CIRCUIT_STATE.labels(self.name).set(0)
                return
            self._outcomes.append((now, True))
            self._prune(now)

    async def failure(self, probe: bool) -> None:
        now = monotonic()
        async with self._lock:
            if probe:
                self._probe_in_flight = max(0, self._probe_in_flight - 1)
                self._probe_successes = 0
                self._open_until = now + self.open_seconds
                CIRCUIT_STATE.labels(self.name).set(1)
                return
            self._outcomes.append((now, False))
            self._prune(now)
            if len(self._outcomes) < self.minimum_requests:
                return
            failures = sum(1 for _, ok in self._outcomes if not ok)
            if failures / len(self._outcomes) >= self.failure_ratio:
                self._open_until = now + self.open_seconds
                self._probe_successes = 0
                CIRCUIT_STATE.labels(self.name).set(1)


telegram_circuit = CircuitBreaker("telegram")
s3_circuit = CircuitBreaker("s3", minimum_requests=10, open_seconds=30)
