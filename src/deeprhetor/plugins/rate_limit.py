"""Async rate limiting and concurrency controls for providers."""

from __future__ import annotations

import asyncio
import time


class AsyncRateLimiter:
    """Simple minimum-interval rate limiter (requests per minute)."""

    def __init__(self, rate_per_minute: float | None) -> None:
        if rate_per_minute is None or rate_per_minute <= 0:
            self._min_interval = 0.0
        else:
            self._min_interval = 60.0 / float(rate_per_minute)
        self._lock = asyncio.Lock()
        self._last = 0.0

    @property
    def min_interval(self) -> float:
        return self._min_interval

    async def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class ProviderGate:
    """Combine per-provider rate limiting with shared concurrency."""

    def __init__(
        self,
        *,
        rate_per_minute: float | None = None,
        concurrency: int = 4,
        rate_limiter: AsyncRateLimiter | None = None,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self.rate_limiter = rate_limiter or AsyncRateLimiter(rate_per_minute)
        limit = max(1, concurrency)
        self._semaphore = semaphore or asyncio.Semaphore(limit)
        self._owns_semaphore = semaphore is None

    async def __aenter__(self) -> ProviderGate:
        await self._semaphore.acquire()
        await self.rate_limiter.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._semaphore.release()
