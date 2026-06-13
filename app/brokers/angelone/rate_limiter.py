"""
Process-wide async rate limiter for Angel One SmartAPI.

Serialises outbound HTTP so bulk ingestion (NIFTY50 sync, shortlist full
pipeline) does not burst past Angel One's published caps (~3 req/s and
~180 req/min for historical data).

A Redis-backed job queue (arq / BullMQ-style) can be added later for
durability and multi-instance deployments; this module is the lightweight
guard until then.

Usage:
    from app.brokers.angelone.rate_limiter import angel_one_rate_limiter

    async def call_api(...):
        await angel_one_rate_limiter.acquire()
        ...
"""

from __future__ import annotations

import asyncio
import time

from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AngelOneRateLimiter:
    """
    Enforces minimum spacing between requests and a rolling per-minute cap.

    All concurrent callers share one limiter instance so INGESTION_CONCURRENCY
    only controls parallelism — actual request timing is centralised here.
    """

    def __init__(
        self,
        requests_per_second: float,
        requests_per_minute: int,
        min_spacing_seconds: float,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")

        self._per_second = requests_per_second
        self._per_minute = requests_per_minute
        # Spacing from rate cap and optional floor (INGESTION_API_DELAY_SECONDS).
        self._min_spacing = max(1.0 / requests_per_second, min_spacing_seconds)
        self._lock = asyncio.Lock()
        self._recent: list[float] = []  # monotonic timestamps of acquire() calls
        self._paused_until: float = 0.0

    @property
    def min_spacing_seconds(self) -> float:
        return self._min_spacing

    async def acquire(self) -> None:
        """Wait until a request slot is available, then record the send time."""
        async with self._lock:
            while True:
                now = time.monotonic()

                if now < self._paused_until:
                    await asyncio.sleep(self._paused_until - now)
                    continue

                self._prune(now - 60.0)

                if len(self._recent) >= self._per_minute:
                    oldest = self._recent[0]
                    wait = 60.0 - (now - oldest) + 0.05
                    logger.debug(
                        "Angel One per-minute cap (%d/min) reached; sleeping %.2fs",
                        self._per_minute,
                        wait,
                    )
                    await asyncio.sleep(max(wait, 0.05))
                    continue

                if self._recent:
                    since_last = now - self._recent[-1]
                    if since_last < self._min_spacing:
                        await asyncio.sleep(self._min_spacing - since_last)
                        continue

                self._recent.append(time.monotonic())
                return

    async def pause(self, seconds: float) -> None:
        """
        Global cooldown after HTTP 429 — all callers block until it expires.
        """
        if seconds <= 0:
            return
        async with self._lock:
            until = time.monotonic() + seconds
            if until > self._paused_until:
                self._paused_until = until
                logger.warning(
                    "Angel One rate limit: pausing all API calls for %.0fs",
                    seconds,
                )

    def _prune(self, cutoff: float) -> None:
        self._recent = [t for t in self._recent if t > cutoff]


def _build_limiter() -> AngelOneRateLimiter:
    return AngelOneRateLimiter(
        requests_per_second=settings.ANGELONE_API_RATE_PER_SECOND,
        requests_per_minute=settings.ANGELONE_API_RATE_PER_MINUTE,
        min_spacing_seconds=settings.INGESTION_API_DELAY_SECONDS,
    )


# Shared by auth + historical clients (and any future Angel One callers).
angel_one_rate_limiter = _build_limiter()
