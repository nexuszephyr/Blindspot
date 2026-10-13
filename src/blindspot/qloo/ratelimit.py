"""Async token bucket shared by all callers in the process."""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate: float, capacity: float | None = None, clock=time.monotonic):
        self.rate = max(rate, 0.01)
        self.capacity = capacity if capacity is not None else max(1.0, self.rate)
        self.tokens = self.capacity
        self.clock = clock
        self.updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    async def acquire(self) -> None:
        async with self._lock:  # serializes waiters so tokens are handed out in FIFO order
            while True:
                self._refill()
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)
