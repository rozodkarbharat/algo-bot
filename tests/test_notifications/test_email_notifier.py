"""
Unit tests for EmailNotifier.

SMTP is always patched — no real connections are made.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.notifications.base_notifier import AlertSeverity, NotificationEventType
from app.notifications.email_notifier import EmailNotifier


@pytest.fixture
def enabled_settings(monkeypatch):
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_ENABLED", True)
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_FROM", "bot@example.com")
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_TO", "trader@example.com")
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_SMTP_USER", "user")
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_SMTP_PASSWORD", "pass")
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_SMTP_PORT", 587)


@pytest.fixture
def disabled_settings(monkeypatch):
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_ENABLED", False)
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_FROM", "")
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_TO", "")
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_SMTP_USER", "")


@pytest.fixture
def notifier():
    return EmailNotifier()


# ── is_enabled ────────────────────────────────────────────────────────────────

def test_is_enabled_true(enabled_settings, notifier):
    assert notifier.is_enabled is True


def test_is_enabled_false_missing_from(monkeypatch, notifier):
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_ENABLED", True)
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_FROM", "")
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_TO", "t@e.com")
    monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_SMTP_USER", "u")
    assert notifier.is_enabled is False


def test_channel_name(notifier):
    assert notifier.channel_name == "email"


# ── send_message ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_success(enabled_settings, notifier):
    with patch.object(notifier, "_smtp_send") as mock_smtp:
        result = await notifier.send_message(
            title="Test Alert",
            body="This is a test",
            severity=AlertSeverity.INFO,
        )
    assert result is True
    mock_smtp.assert_called_once()


@pytest.mark.asyncio
async def test_send_message_disabled(disabled_settings, notifier):
    result = await notifier.send_message(title="Test", body="Hello")
    assert result is False


@pytest.mark.asyncio
async def test_send_message_retries_on_failure(enabled_settings, notifier, monkeypatch):
    monkeypatch.setattr("app.notifications.email_notifier._MAX_RETRIES", 3)
    monkeypatch.setattr("app.notifications.email_notifier._BACKOFF_BASE", 0.01)

    call_count = 0

    def flaky_smtp(*args):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionRefusedError("SMTP unavailable")

    with patch.object(notifier, "_smtp_send", side_effect=flaky_smtp):
        result = await notifier.send_message(title="Test", body="Hello")

    assert result is True
    assert call_count == 3


@pytest.mark.asyncio
async def test_send_message_exhausts_retries(enabled_settings, notifier, monkeypatch):
    monkeypatch.setattr("app.notifications.email_notifier._MAX_RETRIES", 2)
    monkeypatch.setattr("app.notifications.email_notifier._BACKOFF_BASE", 0.01)

    with patch.object(notifier, "_smtp_send", side_effect=ConnectionRefusedError("always fail")):
        result = await notifier.send_message(title="Test", body="Hello")

    assert result is False


# ── send_trade_alert ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_trade_entered(enabled_settings, notifier):
    with patch.object(notifier, "_smtp_send") as mock_smtp:
        result = await notifier.send_trade_alert(
            event_type=NotificationEventType.PAPER_TRADE_ENTERED,
            symbol="HDFC",
            side="LONG",
            entry_price=1650.0,
            stop_loss=1620.0,
            quantity=60,
            extra={"mode": "Paper", "trading_date": "2026-05-29"},
        )
    assert result is True
    mock_smtp.assert_called_once()


@pytest.mark.asyncio
async def test_send_trade_exited(enabled_settings, notifier):
    with patch.object(notifier, "_smtp_send") as mock_smtp:
        result = await notifier.send_trade_alert(
            event_type=NotificationEventType.PAPER_TRADE_EXITED,
            symbol="HDFC",
            side="LONG",
            entry_price=1650.0,
            stop_loss=1620.0,
            quantity=60,
            pnl=900.0,
            extra={"mode": "Paper", "exit_price": 1665.0, "exit_reason": "EOD_EXIT"},
        )
    assert result is True


@pytest.mark.asyncio
async def test_send_sl_hit(enabled_settings, notifier):
    with patch.object(notifier, "_smtp_send") as mock_smtp:
        result = await notifier.send_trade_alert(
            event_type=NotificationEventType.STOP_LOSS_HIT,
            symbol="WIPRO",
            side="SHORT",
            entry_price=480.0,
            stop_loss=490.0,
            quantity=200,
            pnl=-2000.0,
        )
    assert result is True


# ── send_system_alert ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_system_error(enabled_settings, notifier):
    with patch.object(notifier, "_smtp_send") as mock_smtp:
        result = await notifier.send_error(
            component="scheduler",
            error="job failed",
            detail="traceback goes here",
        )
    assert result is True


# ── _smtp_send structure ──────────────────────────────────────────────────────

def test_smtp_send_calls_starttls(enabled_settings, notifier):
    """Verify the SMTP send path calls starttls (no real connection)."""
    import smtplib

    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_server):
        notifier._smtp_send(
            subject="[TradingBot] Test",
            plain="Test body",
            html="<p>Test body</p>",
        )

    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once()
    mock_server.sendmail.assert_called_once()


def test_smtp_send_multiple_recipients(enabled_settings, notifier, monkeypatch):
    monkeypatch.setattr(
        "app.notifications.email_notifier.settings.ALERT_EMAIL_TO",
        "a@example.com, b@example.com, c@example.com",
    )
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_server):
        notifier._smtp_send("subj", "plain", "<p>html</p>")

    call_args = mock_server.sendmail.call_args
    recipients_arg = call_args[0][1]
    assert len(recipients_arg) == 3
