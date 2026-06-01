"""
Unit tests for NotificationManager.

All DB interactions are mocked. Provider send methods are mocked via
a FakeProvider implementation of BaseNotifier.
"""

import pytest
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.alert_event import AlertEvent, AlertSeverity, AlertChannel
from app.notifications.base_notifier import AlertSeverity as Sev, BaseNotifier, NotificationEventType
from app.notifications.notification_manager import NotificationManager


# ── Fake provider ─────────────────────────────────────────────────────────────

class FakeProvider(BaseNotifier):
    def __init__(self, enabled: bool = True, will_succeed: bool = True) -> None:
        self._enabled = enabled
        self._will_succeed = will_succeed
        self.calls: list[dict] = []

    @property
    def channel_name(self) -> str:
        return "fake"

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    async def send_message(self, title, body, severity=Sev.INFO, payload=None) -> bool:
        self.calls.append({"method": "send_message", "title": title})
        return self._will_succeed

    async def send_error(self, component, error, detail="") -> bool:
        self.calls.append({"method": "send_error", "component": component})
        return self._will_succeed

    async def send_trade_alert(self, event_type, symbol, side, entry_price, stop_loss, quantity, pnl=None, extra=None) -> bool:
        self.calls.append({"method": "send_trade_alert", "symbol": symbol})
        return self._will_succeed

    async def send_system_alert(self, event_type, message, payload=None) -> bool:
        self.calls.append({"method": "send_system_alert", "event_type": str(event_type)})
        return self._will_succeed


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_manager(provider: Optional[BaseNotifier] = None) -> NotificationManager:
    mgr = NotificationManager()
    if provider:
        mgr.register_provider(provider)
    return mgr


def _mock_persist(mock_event: AlertEvent = None):
    """Return a mock for _persist_event."""
    if mock_event is None:
        mock_event = MagicMock(spec=AlertEvent)
        mock_event.event_type = "test_event"
        mock_event.severity = "info"
        mock_event.title = "Test"
        mock_event.body = "body"
        mock_event.payload = {}
        mock_event.save = AsyncMock()
    return AsyncMock(return_value=mock_event)


# ── register_provider / active_providers ──────────────────────────────────────

def test_register_provider():
    mgr = _make_manager()
    p = FakeProvider(enabled=True)
    mgr.register_provider(p)
    assert p in mgr.get_providers()


def test_active_providers_excludes_disabled():
    mgr = _make_manager()
    p_enabled = FakeProvider(enabled=True)
    p_disabled = FakeProvider(enabled=False)
    mgr.register_provider(p_enabled)
    mgr.register_provider(p_disabled)
    active = mgr.active_providers
    assert p_enabled in active
    assert p_disabled not in active


# ── dedup check ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_suppressed_when_duplicate():
    mgr = _make_manager(FakeProvider())
    mgr._repo.find_recent_by_dedup_key = AsyncMock(return_value=MagicMock())  # found → suppress
    result = await mgr.dispatch(
        event_type=NotificationEventType.SIGNAL_GENERATED,
        title="Test",
        body="body",
    )
    assert result is None


@pytest.mark.asyncio
async def test_dispatch_sends_when_no_duplicate():
    provider = FakeProvider()
    mgr = _make_manager(provider)
    mgr._repo.find_recent_by_dedup_key = AsyncMock(return_value=None)  # not found → allow

    fake_event = MagicMock(spec=AlertEvent)
    fake_event.event_type = "signal_generated"
    fake_event.severity = "info"
    fake_event.title = "Test"
    fake_event.body = "body"
    fake_event.payload = {}
    fake_event.save = AsyncMock()

    mgr._persist_event = _mock_persist(fake_event)
    mgr._mark_delivered = AsyncMock()

    result = await mgr.dispatch(
        event_type=NotificationEventType.SIGNAL_GENERATED,
        title="Test",
        body="body",
    )
    assert result is fake_event
    assert len(provider.calls) == 1


# ── dispatch_trade_alert ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_trade_alert_calls_provider():
    provider = FakeProvider()
    mgr = _make_manager(provider)
    mgr._repo.find_recent_by_dedup_key = AsyncMock(return_value=None)

    fake_event = MagicMock(spec=AlertEvent)
    fake_event.save = AsyncMock()
    mgr._persist_event = _mock_persist(fake_event)
    mgr._mark_delivered = AsyncMock()

    await mgr.dispatch_trade_alert(
        event_type=NotificationEventType.PAPER_TRADE_ENTERED,
        symbol="RELIANCE",
        side="LONG",
        entry_price=2540.0,
        stop_loss=2490.0,
        quantity=39,
    )
    assert any(c["method"] == "send_trade_alert" for c in provider.calls)


@pytest.mark.asyncio
async def test_dispatch_trade_alert_deduped():
    provider = FakeProvider()
    mgr = _make_manager(provider)
    mgr._repo.find_recent_by_dedup_key = AsyncMock(return_value=MagicMock())

    result = await mgr.dispatch_trade_alert(
        event_type=NotificationEventType.PAPER_TRADE_ENTERED,
        symbol="RELIANCE",
        side="LONG",
        entry_price=2540.0,
        stop_loss=2490.0,
        quantity=39,
    )
    assert result is None
    assert len(provider.calls) == 0


# ── dispatch_system_alert ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_system_alert_critical():
    provider = FakeProvider()
    mgr = _make_manager(provider)
    mgr._repo.find_recent_by_dedup_key = AsyncMock(return_value=None)

    fake_event = MagicMock(spec=AlertEvent)
    fake_event.save = AsyncMock()
    mgr._persist_event = _mock_persist(fake_event)
    mgr._mark_delivered = AsyncMock()

    result = await mgr.dispatch_system_alert(
        event_type=NotificationEventType.BROKER_DISCONNECTED,
        message="Connection lost",
        payload={"broker": "AngelOne", "reason": "timeout"},
        severity=Sev.CRITICAL,
    )
    assert result is fake_event
    assert any(c["method"] == "send_system_alert" for c in provider.calls)


# ── throttle window applied from settings ─────────────────────────────────────

def test_throttle_window_from_settings(monkeypatch):
    from app.config.settings import settings
    monkeypatch.setattr(settings, "NOTIFY_THROTTLE_WINDOW_SECONDS", 999)
    mgr = NotificationManager()
    assert mgr._throttle_window == 999


# ── choose_channel fallback ───────────────────────────────────────────────────

def test_choose_channel_returns_system_when_no_providers():
    mgr = _make_manager()
    channel = mgr._choose_channel()
    assert channel == AlertChannel.SYSTEM


def test_choose_channel_returns_system_for_unknown_channel_name():
    """Provider with a channel_name not in AlertChannel enum → fallback to SYSTEM."""
    mgr = _make_manager(FakeProvider(enabled=True))
    channel = mgr._choose_channel()
    assert channel == AlertChannel.SYSTEM  # "fake" is not a valid AlertChannel value


# ── dedup check exception tolerance ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_is_duplicate_allows_send_on_exception():
    """If the dedup DB call throws, we allow the send (fail-open)."""
    mgr = _make_manager()
    mgr._repo.find_recent_by_dedup_key = AsyncMock(side_effect=Exception("DB down"))
    result = await mgr._is_duplicate("some_key")
    assert result is False
