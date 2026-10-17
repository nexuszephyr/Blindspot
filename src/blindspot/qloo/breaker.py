"""Process-wide circuit breaker for 429 storms.

closed --(N consecutive 429s)--> open --(cooldown elapses)--> half_open --(one probe)-->
closed on success, open again on another 429.
"""

from __future__ import annotations

import threading
import time


class CircuitBreaker:
    def __init__(self, threshold: int = 3, cooldown_s: float = 30.0, clock=time.monotonic):
        self.threshold = max(1, threshold)
        self.cooldown_s = cooldown_s
        self.clock = clock
        self._lock = threading.Lock()
        self._consecutive = 0
        self._open_until = 0.0
        self._opened = False
        self._probe_in_flight = False
        self.trips = 0

    @property
    def state(self) -> str:
        with self._lock:
            return self._state()

    def _state(self) -> str:
        if not self._opened:
            return "closed"
        return "open" if self.clock() < self._open_until else "half_open"

    def allow(self) -> tuple[bool, bool]:
        """Returns (allowed, is_probe). The caller that gets is_probe=True holds the half-open
        probe slot and must report its outcome with probe=True; nobody else can release it (#78)."""
        with self._lock:
            st = self._state()
            if st == "closed":
                return True, False
            if st == "half_open" and not self._probe_in_flight:
                self._probe_in_flight = True
                return True, True
            return False, False

    def retry_in(self) -> float:
        with self._lock:
            return max(0.0, self._open_until - self.clock()) if self._opened else 0.0

    def record_success(self, probe: bool = False) -> None:
        with self._lock:
            self._consecutive = 0
            self._opened = False
            self._probe_in_flight = False  # closed: no probe is meaningful any more

    def record_failure_other(self, probe: bool = False) -> None:
        """A non-429 outcome (error, timeout, cancellation): the probe holder frees its slot."""
        with self._lock:
            if probe:
                self._probe_in_flight = False

    def record_429(self, retry_after: float | None = None, probe: bool = False) -> None:
        with self._lock:
            self._consecutive += 1
            if probe:
                self._probe_in_flight = False
            if probe or self._consecutive >= self.threshold:
                self._opened = True
                self._open_until = self.clock() + max(self.cooldown_s, retry_after or 0.0)
                self.trips += 1
