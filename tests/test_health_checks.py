"""
Unit tests for the health check framework.

Tests the base class behaviour and verifies that each check:
  - Returns the correct status shape
  - Handles component failures gracefully (never raises)
  - Correctly classifies results as healthy/degraded/unhealthy
"""

from __future__ import annotations

import pytest

from app.monitoring.health_checks.base import BaseHealthCheck, ComponentHealthResult


# ── Base class tests ──────────────────────────────────────────────────────────

class _AlwaysOkCheck(BaseHealthCheck):
    @property
    def component_name(self) -> str:
        return "test_ok"

    async def _run(self) -> ComponentHealthResult:
        return ComponentHealthResult.ok("test_ok", latency_ms=5.0, extra="value")


class _AlwaysCrashCheck(BaseHealthCheck):
    @property
    def component_name(self) -> str:
        return "test_crash"

    async def _run(self) -> ComponentHealthResult:
        raise RuntimeError("Simulated crash")


class _DegradedCheck(BaseHealthCheck):
    @property
    def component_name(self) -> str:
        return "test_degraded"

    async def _run(self) -> ComponentHealthResult:
        return ComponentHealthResult.degraded(
            "test_degraded",
            latency_ms=150.0,
            message="Slow but alive",
        )


class _UnhealthyCheck(BaseHealthCheck):
    @property
    def component_name(self) -> str:
        return "test_unhealthy"

    async def _run(self) -> ComponentHealthResult:
        return ComponentHealthResult.unhealthy(
            "test_unhealthy",
            message="Component is down",
            error_count=5,
        )


@pytest.mark.asyncio
async def test_ok_check_returns_healthy():
    result = await _AlwaysOkCheck().run()
    assert result.healthy is True
    assert result.status == "healthy"
    assert result.error_count == 0
    assert result.error_message is None


@pytest.mark.asyncio
async def test_ok_check_has_latency():
    result = await _AlwaysOkCheck().run()
    assert result.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_crashed_check_returns_unhealthy():
    result = await _AlwaysCrashCheck().run()
    assert result.healthy is False
    assert result.status == "unhealthy"
    assert "Simulated crash" in (result.error_message or "")


@pytest.mark.asyncio
async def test_crashed_check_never_raises():
    # run() must not propagate exceptions
    result = await _AlwaysCrashCheck().run()
    assert result is not None


@pytest.mark.asyncio
async def test_degraded_check():
    result = await _DegradedCheck().run()
    assert result.healthy is False
    assert result.status == "degraded"
    assert result.latency_ms == pytest.approx(150.0, abs=1.0)


@pytest.mark.asyncio
async def test_unhealthy_check():
    result = await _UnhealthyCheck().run()
    assert result.healthy is False
    assert result.status == "unhealthy"
    assert result.error_count == 5


# ── ComponentHealthResult factory methods ─────────────────────────────────────

def test_ok_factory():
    r = ComponentHealthResult.ok("comp", latency_ms=10.0, key="val")
    assert r.healthy is True
    assert r.status == "healthy"
    assert r.metadata == {"key": "val"}
    assert r.latency_ms == pytest.approx(10.0)


def test_degraded_factory():
    r = ComponentHealthResult.degraded("comp", latency_ms=200.0, message="slow", count=3)
    assert r.healthy is False
    assert r.status == "degraded"
    assert r.error_message == "slow"


def test_unhealthy_factory():
    r = ComponentHealthResult.unhealthy("comp", message="down", error_count=10)
    assert r.healthy is False
    assert r.status == "unhealthy"
    assert r.error_count == 10
    assert r.latency_ms == 0.0


# ── MongoDB check (mocked) ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mongodb_check_unhealthy_when_db_unreachable():
    from app.monitoring.health_checks.mongodb_check import MongoDBHealthCheck
    from unittest.mock import AsyncMock, patch

    with patch("app.monitoring.health_checks.mongodb_check.get_database") as mock_db:
        mock_db.return_value.command = AsyncMock(side_effect=Exception("connection refused"))
        result = await MongoDBHealthCheck().run()

    assert result.healthy is False
    assert result.status == "unhealthy"
    assert "connection refused" in (result.error_message or "")


@pytest.mark.asyncio
async def test_mongodb_check_healthy_when_ping_succeeds():
    from app.monitoring.health_checks.mongodb_check import MongoDBHealthCheck
    from unittest.mock import AsyncMock, patch

    with patch("app.monitoring.health_checks.mongodb_check.get_database") as mock_db:
        mock_db.return_value.command = AsyncMock(return_value={"ok": 1})
        result = await MongoDBHealthCheck().run()

    assert result.healthy is True
    assert result.status == "healthy"


# ── Scheduler check (mocked) ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scheduler_check_unhealthy_when_not_running():
    from app.monitoring.health_checks.scheduler_check import SchedulerHealthCheck
    from unittest.mock import MagicMock, patch

    mock_scheduler = MagicMock()
    mock_scheduler.running = False

    with patch("app.scheduler.scheduler.scheduler", mock_scheduler):
        result = await SchedulerHealthCheck().run()

    assert result.healthy is False
    assert result.status == "unhealthy"


@pytest.mark.asyncio
async def test_scheduler_check_degraded_when_no_jobs():
    from app.monitoring.health_checks.scheduler_check import SchedulerHealthCheck
    from unittest.mock import MagicMock, patch

    mock_scheduler = MagicMock()
    mock_scheduler.running = True
    mock_scheduler.get_jobs.return_value = []

    with patch("app.scheduler.scheduler.scheduler", mock_scheduler):
        result = await SchedulerHealthCheck().run()

    assert result.status == "degraded"


@pytest.mark.asyncio
async def test_scheduler_check_healthy_with_jobs():
    from app.monitoring.health_checks.scheduler_check import SchedulerHealthCheck
    from unittest.mock import MagicMock, patch
    from datetime import datetime, timezone

    mock_job = MagicMock()
    mock_job.id = "test_job"
    mock_job.next_run_time = datetime(2025, 1, 15, 9, 30, tzinfo=timezone.utc)

    mock_scheduler = MagicMock()
    mock_scheduler.running = True
    mock_scheduler.get_jobs.return_value = [mock_job]

    with patch("app.scheduler.scheduler.scheduler", mock_scheduler):
        result = await SchedulerHealthCheck().run()

    assert result.healthy is True
    assert result.metadata["job_count"] == 1
