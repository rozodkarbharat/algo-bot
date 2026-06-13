"""Tests for the process-wide Angel One rate limiter."""

import asyncio
import time

import pytest

from app.brokers.angelone.historical_data import _is_rate_limit_body
from app.brokers.angelone.rate_limiter import AngelOneRateLimiter


@pytest.mark.parametrize(
    "body",
    [
        "Access denied because of exceeding access rate",
        "EXCEEDING ACCESS RATE",
        "you have hit the rate limit",
    ],
)
def test_is_rate_limit_body_detects_rate_limit_phrases(body: str) -> None:
    assert _is_rate_limit_body(body) is True


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "Invalid token",
        "AB1010: session expired",
    ],
)
def test_is_rate_limit_body_ignores_auth_errors(body: str) -> None:
    assert _is_rate_limit_body(body) is False


@pytest.mark.asyncio
async def test_acquire_enforces_minimum_spacing() -> None:
    limiter = AngelOneRateLimiter(
        requests_per_second=5.0,
        requests_per_minute=100,
        min_spacing_seconds=0.2,
    )
    t0 = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.18


@pytest.mark.asyncio
async def test_pause_blocks_all_callers() -> None:
    limiter = AngelOneRateLimiter(
        requests_per_second=10.0,
        requests_per_minute=100,
        min_spacing_seconds=0.0,
    )
    await limiter.pause(0.15)
    t0 = time.monotonic()
    await limiter.acquire()
    assert time.monotonic() - t0 >= 0.1


@pytest.mark.asyncio
async def test_concurrent_acquires_are_serialized() -> None:
    limiter = AngelOneRateLimiter(
        requests_per_second=2.0,
        requests_per_minute=60,
        min_spacing_seconds=0.5,
    )

    async def one() -> None:
        await limiter.acquire()

    t0 = time.monotonic()
    await asyncio.gather(one(), one(), one())
    assert time.monotonic() - t0 >= 0.9
