"""
Unit tests for NotificationService.

The notification_manager and ws_manager singletons are mocked so
no real DB, HTTP, or WebSocket calls are made.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.notifications.base_notifier import NotificationEventType
from app.services.notification_service import NotificationService


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def svc():
    return NotificationService()


@pytest.fixture
def mock_manager():
    m = MagicMock()
    m.dispatch = AsyncMock(return_value=MagicMock())
    m.dispatch_trade_alert = AsyncMock(return_value=MagicMock())
    m.dispatch_system_alert = AsyncMock(return_value=MagicMock())
    m.dispatch_daily_summary = AsyncMock(return_value=MagicMock())
    return m


@pytest.fixture
def mock_ws():
    m = MagicMock()
    m.broadcast_to_room = AsyncMock()
    return m


def _patch_all(svc, mock_manager, mock_ws, monkeypatch):
    monkeypatch.setattr("app.services.notification_service.notification_manager", mock_manager)
    monkeypatch.setattr("app.services.notification_service.ws_manager", mock_ws)


def _enable_all(monkeypatch):
    monkeypatch.setattr("app.services.notification_service.settings.NOTIFY_ENABLED", True)
    monkeypatch.setattr("app.services.notification_service.settings.NOTIFY_TRADE_ALERTS", True)
    monkeypatch.setattr("app.services.notification_service.settings.NOTIFY_SIGNAL_ALERTS", True)
    monkeypatch.setattr("app.services.notification_service.settings.NOTIFY_SYSTEM_ALERTS", True)
    monkeypatch.setattr("app.services.notification_service.settings.NOTIFY_DAILY_SUMMARY", True)


# ── on_signal_generated ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_signal_generated_dispatches(svc, mock_manager, mock_ws, monkeypatch):
    _enable_all(monkeypatch)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)

    await svc.on_signal_generated("RELIANCE", "BUY", 2540.0, 2490.0, prob=0.71)

    mock_manager.dispatch.assert_called_once()
    call_kwargs = mock_manager.dispatch.call_args.kwargs
    assert call_kwargs["event_type"] == NotificationEventType.SIGNAL_GENERATED
    assert "RELIANCE" in call_kwargs["title"]


@pytest.mark.asyncio
async def test_on_signal_generated_disabled(svc, mock_manager, mock_ws, monkeypatch):
    monkeypatch.setattr("app.services.notification_service.settings.NOTIFY_ENABLED", False)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)

    await svc.on_signal_generated("RELIANCE", "BUY", 2540.0, 2490.0)
    mock_manager.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_on_signal_generated_signal_alerts_off(svc, mock_manager, mock_ws, monkeypatch):
    monkeypatch.setattr("app.services.notification_service.settings.NOTIFY_ENABLED", True)
    monkeypatch.setattr("app.services.notification_service.settings.NOTIFY_SIGNAL_ALERTS", False)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)

    await svc.on_signal_generated("RELIANCE", "BUY", 2540.0, 2490.0)
    mock_manager.dispatch.assert_not_called()


# ── on_paper_trade_entered ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_paper_trade_entered(svc, mock_manager, mock_ws, monkeypatch):
    _enable_all(monkeypatch)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)

    await svc.on_paper_trade_entered("HDFC", "LONG", 1650.0, 1620.0, 60)

    mock_manager.dispatch_trade_alert.assert_called_once()
    assert mock_manager.dispatch_trade_alert.call_args.kwargs["event_type"] == NotificationEventType.PAPER_TRADE_ENTERED


# ── on_stop_loss_hit ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_stop_loss_hit(svc, mock_manager, mock_ws, monkeypatch):
    _enable_all(monkeypatch)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)

    await svc.on_stop_loss_hit("TCS", "SHORT", 3850.0, 3800.0, 26, pnl=-1300.0)

    mock_manager.dispatch_trade_alert.assert_called_once()
    assert mock_manager.dispatch_trade_alert.call_args.kwargs["event_type"] == NotificationEventType.STOP_LOSS_HIT


# ── on_broker_disconnected ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_broker_disconnected(svc, mock_manager, mock_ws, monkeypatch):
    _enable_all(monkeypatch)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)

    await svc.on_broker_disconnected("AngelOne", "timeout")

    mock_manager.dispatch_system_alert.assert_called_once()
    assert mock_manager.dispatch_system_alert.call_args.kwargs["event_type"] == NotificationEventType.BROKER_DISCONNECTED


@pytest.mark.asyncio
async def test_on_broker_disconnected_system_alerts_off(svc, mock_manager, mock_ws, monkeypatch):
    monkeypatch.setattr("app.services.notification_service.settings.NOTIFY_ENABLED", True)
    monkeypatch.setattr("app.services.notification_service.settings.NOTIFY_SYSTEM_ALERTS", False)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)

    await svc.on_broker_disconnected("AngelOne", "timeout")
    mock_manager.dispatch_system_alert.assert_not_called()


# ── on_scheduler_failure ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_scheduler_failure(svc, mock_manager, mock_ws, monkeypatch):
    _enable_all(monkeypatch)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)

    await svc.on_scheduler_failure("eod_sync_job", "MongoDB connection refused")

    mock_manager.dispatch_system_alert.assert_called_once()
    kwargs = mock_manager.dispatch_system_alert.call_args.kwargs
    assert kwargs["event_type"] == NotificationEventType.SCHEDULER_FAILURE
    assert "eod_sync_job" in kwargs["payload"]["job_id"]


# ── on_system_error ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_system_error(svc, mock_manager, mock_ws, monkeypatch):
    _enable_all(monkeypatch)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)

    await svc.on_system_error("CandelBuilder", "KeyError: volume", "line 42")

    mock_manager.dispatch_system_alert.assert_called_once()
    kwargs = mock_manager.dispatch_system_alert.call_args.kwargs
    assert kwargs["event_type"] == NotificationEventType.SYSTEM_ERROR


# ── send_daily_summary ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_daily_summary(svc, mock_manager, mock_ws, monkeypatch):
    _enable_all(monkeypatch)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)

    fake_summary = {
        "trading_date": "29 May 2026",
        "total_signals": 5,
        "total_trades": 3,
        "winning_trades": 2,
        "losing_trades": 1,
        "realized_pnl": 1500.0,
        "unrealized_pnl": 0.0,
        "total_pnl": 1500.0,
        "win_rate": 66.7,
        "top_stock": "RELIANCE",
        "top_stock_pnl": 900.0,
        "worst_stock": "WIPRO",
        "worst_stock_pnl": -300.0,
        "mode": "Paper",
    }

    # Patch build_daily_summary at the service's import namespace
    with patch(
        "app.services.notification_service.notification_service.__class__.send_daily_summary",
        new=None,  # replaced below via direct attribute override
    ):
        pass  # not using this approach

    import app.notifications.daily_summary as _ds_mod
    original_build = _ds_mod.build_daily_summary
    _ds_mod.build_daily_summary = AsyncMock(return_value=fake_summary)

    try:
        await svc.send_daily_summary(mode="Paper")
    finally:
        _ds_mod.build_daily_summary = original_build

    mock_manager.dispatch_daily_summary.assert_called_once()
    mock_ws.broadcast_to_room.assert_called_once()
    ws_call = mock_ws.broadcast_to_room.call_args
    assert ws_call[0][1] == "notifications"
    assert ws_call[0][0]["type"] == "daily_summary"


@pytest.mark.asyncio
async def test_send_daily_summary_disabled(svc, mock_manager, mock_ws, monkeypatch):
    monkeypatch.setattr("app.services.notification_service.settings.NOTIFY_ENABLED", True)
    monkeypatch.setattr("app.services.notification_service.settings.NOTIFY_DAILY_SUMMARY", False)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)

    await svc.send_daily_summary(mode="Paper")
    mock_manager.dispatch_daily_summary.assert_not_called()


# ── WebSocket broadcast ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ws_broadcast_on_signal(svc, mock_manager, mock_ws, monkeypatch):
    _enable_all(monkeypatch)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)

    await svc.on_signal_generated("INFY", "SELL", 1400.0, 1430.0, prob=0.65)

    mock_ws.broadcast_to_room.assert_called()
    ws_call_args = mock_ws.broadcast_to_room.call_args[0]
    assert ws_call_args[1] == "notifications"


@pytest.mark.asyncio
async def test_no_exception_propagated_on_ws_failure(svc, mock_manager, mock_ws, monkeypatch):
    """Service should swallow WS failures and not raise."""
    _enable_all(monkeypatch)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)
    mock_ws.broadcast_to_room = AsyncMock(side_effect=RuntimeError("WS down"))

    # Should not raise
    await svc.on_broker_disconnected("AngelOne", "timeout")


# ── No exception propagated from service ─────────────────────────────────────

@pytest.mark.asyncio
async def test_service_never_raises_on_manager_failure(svc, mock_manager, mock_ws, monkeypatch):
    _enable_all(monkeypatch)
    _patch_all(svc, mock_manager, mock_ws, monkeypatch)
    mock_manager.dispatch.side_effect = Exception("unexpected crash")

    # Must not raise
    await svc.on_signal_generated("RELIANCE", "BUY", 2540.0, 2490.0)
