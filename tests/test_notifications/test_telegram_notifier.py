"""
Unit tests for TelegramNotifier.

All tests use httpx mock transport — no real HTTP calls are made.
Settings are patched via monkeypatch to control is_enabled.
"""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.notifications.base_notifier import AlertSeverity, NotificationEventType
from app.notifications.telegram_notifier import TelegramNotifier


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def enabled_settings(monkeypatch):
    """Patch settings so the Telegram notifier reports is_enabled=True."""
    monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_ENABLED", True)
    monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_CHAT_ID", "12345")


@pytest.fixture
def disabled_settings(monkeypatch):
    monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_ENABLED", False)
    monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_CHAT_ID", "")


@pytest.fixture
def notifier():
    return TelegramNotifier()


# ── is_enabled ────────────────────────────────────────────────────────────────

def test_is_enabled_true(enabled_settings, notifier):
    assert notifier.is_enabled is True


def test_is_enabled_false_no_token(monkeypatch, notifier):
    monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_ENABLED", True)
    monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_CHAT_ID", "123")
    assert notifier.is_enabled is False


def test_channel_name(notifier):
    assert notifier.channel_name == "telegram"


# ── send_message ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_success(enabled_settings, notifier):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await notifier.send_message(
            title="Test", body="Hello world", severity=AlertSeverity.INFO
        )
    assert result is True


@pytest.mark.asyncio
async def test_send_message_disabled(disabled_settings, notifier):
    result = await notifier.send_message(title="Test", body="Hello")
    assert result is False


@pytest.mark.asyncio
async def test_send_message_http_error_retries(enabled_settings, notifier, monkeypatch):
    """Non-5xx errors should not retry."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"
    exc = httpx.HTTPStatusError("401", request=MagicMock(), response=mock_response)

    call_count = 0

    async def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise exc

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_post):
        result = await notifier.send_message(title="Test", body="Hello")

    assert result is False
    assert call_count == 1  # 4xx → no retry


@pytest.mark.asyncio
async def test_send_message_network_error_retries(enabled_settings, notifier, monkeypatch):
    """Network errors should retry up to MAX_RETRIES."""
    monkeypatch.setattr("app.notifications.telegram_notifier._MAX_RETRIES", 2)
    monkeypatch.setattr("app.notifications.telegram_notifier._BACKOFF_BASE", 0.01)

    call_count = 0

    async def flaky_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.ConnectError("network error")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=flaky_post):
        result = await notifier.send_message(title="Test", body="Hello")

    assert result is True
    assert call_count == 2


# ── send_trade_alert ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_trade_alert_entry(enabled_settings, notifier):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await notifier.send_trade_alert(
            event_type=NotificationEventType.PAPER_TRADE_ENTERED,
            symbol="RELIANCE",
            side="LONG",
            entry_price=2540.0,
            stop_loss=2490.0,
            quantity=39,
            extra={"mode": "Paper"},
        )
    assert result is True


@pytest.mark.asyncio
async def test_send_trade_alert_sl_hit(enabled_settings, notifier):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await notifier.send_trade_alert(
            event_type=NotificationEventType.STOP_LOSS_HIT,
            symbol="TCS",
            side="SHORT",
            entry_price=3800.0,
            stop_loss=3850.0,
            quantity=26,
            pnl=-1300.0,
            extra={"mode": "Paper"},
        )
    assert result is True


# ── send_system_alert ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_system_alert_broker_disconnected(enabled_settings, notifier):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await notifier.send_system_alert(
            event_type=NotificationEventType.BROKER_DISCONNECTED,
            message="Lost connection",
            payload={"broker": "AngelOne", "reason": "timeout"},
        )
    assert result is True


# ── Rate limiting ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_inserts_sleep(enabled_settings, notifier, monkeypatch):
    import time as _time

    # Simulate a recent send (just 1ms ago).
    notifier._last_send_at = _time.monotonic() - 0.001

    sleep_called = False

    async def mock_sleep(delay):
        nonlocal sleep_called
        sleep_called = True

    monkeypatch.setattr("asyncio.sleep", mock_sleep)
    await notifier._rate_limit()
    assert sleep_called
