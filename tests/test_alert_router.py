"""
Unit tests for AlertRouter.

Verifies that each routing method calls notification_manager.dispatch_system_alert
with the correct parameters and that failures in the notification system are
never propagated.
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest

from app.models.alert_event import AlertSeverity
from app.monitoring.alert_router import AlertRouter


def _make_router() -> AlertRouter:
    return AlertRouter()


# ── broker_disconnected ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broker_disconnected_dispatches():
    router = _make_router()
    with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
        await router.broker_disconnected("AngelOne", "TCP error")
    mock_dispatch.assert_called_once()
    kwargs = mock_dispatch.call_args.kwargs
    assert "AngelOne" in kwargs["message"]
    assert kwargs["severity"] == AlertSeverity.CRITICAL
    assert "broker_disconnected:AngelOne" == kwargs["dedup_key"]


# ── scheduler_stopped ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scheduler_stopped_dispatches():
    router = _make_router()
    with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
        await router.scheduler_stopped("eod_sync")
    mock_dispatch.assert_called_once()
    kwargs = mock_dispatch.call_args.kwargs
    assert "eod_sync" in kwargs["message"]
    assert kwargs["severity"] == AlertSeverity.CRITICAL


# ── database_unreachable ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_database_unreachable_dispatches():
    router = _make_router()
    with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
        await router.database_unreachable("connection timeout")
    mock_dispatch.assert_called_once()
    kwargs = mock_dispatch.call_args.kwargs
    assert kwargs["dedup_key"] == "database_unreachable"
    assert kwargs["severity"] == AlertSeverity.CRITICAL


# ── market_data_stale ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_market_data_stale_dispatches():
    router = _make_router()
    with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
        await router.market_data_stale(45.0, symbol="RELIANCE")
    mock_dispatch.assert_called_once()
    kwargs = mock_dispatch.call_args.kwargs
    assert kwargs["severity"] == AlertSeverity.WARNING
    assert "market_data_stale:RELIANCE" == kwargs["dedup_key"]


@pytest.mark.asyncio
async def test_market_data_stale_no_symbol():
    router = _make_router()
    with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
        await router.market_data_stale(60.0)
    kwargs = mock_dispatch.call_args.kwargs
    assert "market_data_stale:feed" == kwargs["dedup_key"]


# ── daily_loss_limit_breached ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_loss_limit_dispatches():
    router = _make_router()
    with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
        await router.daily_loss_limit_breached(-25000.0, 20000.0)
    mock_dispatch.assert_called_once()
    kwargs = mock_dispatch.call_args.kwargs
    assert kwargs["severity"] == AlertSeverity.CRITICAL
    assert "25,000" in kwargs["message"]


# ── exposure_limit_warning ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exposure_limit_warning_dispatches():
    router = _make_router()
    with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
        await router.exposure_limit_warning(72.5, 80.0)
    kwargs = mock_dispatch.call_args.kwargs
    assert kwargs["severity"] == AlertSeverity.WARNING
    assert "72.5" in kwargs["message"]


# ── high_rejection_rate ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_high_rejection_rate_dispatches():
    router = _make_router()
    with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
        await router.high_rejection_rate(0.65, 10)
    kwargs = mock_dispatch.call_args.kwargs
    assert "65%" in kwargs["message"]
    assert kwargs["severity"] == AlertSeverity.WARNING


# ── kill_switch_engaged ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_kill_switch_dispatches():
    router = _make_router()
    with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
        await router.kill_switch_engaged("daily_loss_limit")
    kwargs = mock_dispatch.call_args.kwargs
    assert kwargs["severity"] == AlertSeverity.CRITICAL
    assert "kill_switch_engaged" == kwargs["dedup_key"]


# ── escalation_alert ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_escalation_alert_bypasses_normal_dedup():
    router = _make_router()
    with patch("app.notifications.notification_manager.notification_manager") as mock_nm:
        mock_nm.dispatch_system_alert = AsyncMock()
        await router.escalation_alert("mongodb", "abc123", "5 consecutive failures")

    mock_nm.dispatch_system_alert.assert_called_once()
    call_kwargs = mock_nm.dispatch_system_alert.call_args.kwargs
    # Each escalation gets a unique dedup key
    assert "abc123" in call_kwargs["dedup_key"]


# ── Failure isolation ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_failure_never_propagates():
    router = _make_router()
    with patch("app.notifications.notification_manager.notification_manager") as mock_nm:
        mock_nm.dispatch_system_alert = AsyncMock(side_effect=Exception("provider down"))
        # Should not raise
        await router.broker_disconnected("AngelOne", "crash")


# ── Strategy concentration ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_strategy_concentration_warning():
    router = _make_router()
    with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
        await router.strategy_concentration_warning("one_side_orb", 48.0, 50.0)
    kwargs = mock_dispatch.call_args.kwargs
    assert "one_side_orb" in kwargs["message"]
    assert kwargs["severity"] == AlertSeverity.WARNING


@pytest.mark.asyncio
async def test_sector_concentration_warning():
    router = _make_router()
    with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
        await router.sector_concentration_warning("Energy", 38.0, 40.0)
    kwargs = mock_dispatch.call_args.kwargs
    assert "Energy" in kwargs["message"]
