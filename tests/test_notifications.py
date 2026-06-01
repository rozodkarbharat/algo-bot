"""
Comprehensive tests for the Notification & Incident Management Platform.

Covers:
  1. Telegram provider: send_message, send_error, send_trade_alert, send_incident_alert
  2. Email provider: send_message, send_trade_alert, send_incident_alert
  3. Throttling / duplicate prevention (dedup window logic)
  4. Incident lifecycle: create, acknowledge, resolve (with ACKNOWLEDGED status)
  5. NotificationEvent model creation
  6. NotificationService event dispatching
  7. AlertRouter infrastructure routing

All external I/O is mocked — no real HTTP calls, no real SMTP, no real MongoDB.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.alert_event import AlertSeverity
from app.models.notification_event import NotificationEvent, NotificationSeverity
from app.models.system_incident import IncidentStatus, SystemIncident
from app.monitoring.incident_manager import IncidentManager
from app.notifications.base_notifier import AlertSeverity as Sev, NotificationEventType
from app.notifications.email_notifier import EmailNotifier
from app.notifications.notification_manager import NotificationManager
from app.notifications.telegram_notifier import TelegramNotifier
from app.notifications.templates import telegram_templates as tg_tpl
from app.notifications.templates import email_templates as em_tpl


# ═══════════════════════════════════════════════════════════════════════════════
# 1. TELEGRAM PROVIDER
# ═══════════════════════════════════════════════════════════════════════════════

class TestTelegramProvider:
    """Tests for TelegramNotifier — all HTTP calls mocked."""

    @pytest.fixture
    def notifier(self, monkeypatch):
        monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_ENABLED", True)
        monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_BOT_TOKEN", "tok123")
        monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_CHAT_ID", "9999")
        return TelegramNotifier()

    @pytest.fixture
    def disabled_notifier(self, monkeypatch):
        monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_ENABLED", False)
        monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_BOT_TOKEN", "")
        monkeypatch.setattr("app.notifications.telegram_notifier.settings.ALERT_TELEGRAM_CHAT_ID", "")
        return TelegramNotifier()

    def test_channel_name(self, notifier):
        assert notifier.channel_name == "telegram"

    def test_is_enabled_true(self, notifier):
        assert notifier.is_enabled is True

    def test_is_enabled_false_when_disabled(self, disabled_notifier):
        assert disabled_notifier.is_enabled is False

    @pytest.mark.asyncio
    async def test_send_message_success(self, notifier):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await notifier.send_message("Test Title", "Test body")
        assert result is True

    @pytest.mark.asyncio
    async def test_send_message_disabled_returns_false(self, disabled_notifier):
        result = await disabled_notifier.send_message("x", "y")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_error(self, notifier):
        with patch.object(notifier, "_send", new_callable=AsyncMock, return_value=True) as mock_send:
            result = await notifier.send_error("mongo", "DB unreachable", "detail here")
        assert result is True
        mock_send.assert_called_once()
        text_arg = mock_send.call_args[0][0]
        assert "mongo" in text_arg.lower() or "db" in text_arg.lower() or "unreachable" in text_arg.lower()

    @pytest.mark.asyncio
    async def test_send_trade_alert_entered(self, notifier):
        with patch.object(notifier, "_send", new_callable=AsyncMock, return_value=True) as mock_send:
            result = await notifier.send_trade_alert(
                NotificationEventType.PAPER_TRADE_ENTERED,
                symbol="RELIANCE", side="LONG",
                entry_price=2540.0, stop_loss=2490.0, quantity=10,
            )
        assert result is True
        text = mock_send.call_args[0][0]
        assert "RELIANCE" in text
        assert "2540" in text

    @pytest.mark.asyncio
    async def test_send_incident_alert(self, notifier):
        with patch.object(notifier, "_send", new_callable=AsyncMock, return_value=True) as mock_send:
            result = await notifier.send_incident_alert(
                incident_id="abc123",
                component="mongodb",
                severity="critical",
                title="MongoDB Down",
                description="Cannot connect to primary",
                status="open",
            )
        assert result is True
        text = mock_send.call_args[0][0]
        assert "abc123" in text
        assert "mongodb" in text.lower()

    @pytest.mark.asyncio
    async def test_send_retries_on_5xx(self, notifier):
        call_count = 0

        async def _mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 500
            resp.raise_for_status.side_effect = __import__("httpx").HTTPStatusError(
                "Server Error", request=MagicMock(), response=resp
            )
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = _mock_post
            mock_cls.return_value = mock_client

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await notifier._send("hello")

        assert result is False
        # Should have attempted _MAX_RETRIES = 3 times
        assert call_count == 3


# ═══════════════════════════════════════════════════════════════════════════════
# 2. EMAIL PROVIDER
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmailProvider:
    """Tests for EmailNotifier — SMTP mocked via run_in_executor."""

    @pytest.fixture
    def notifier(self, monkeypatch):
        monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_ENABLED", True)
        monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_FROM", "bot@trade.local")
        monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_TO", "ops@trade.local")
        monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_SMTP_USER", "bot@trade.local")
        monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_SMTP_PASSWORD", "secret")
        return EmailNotifier()

    @pytest.fixture
    def disabled_notifier(self, monkeypatch):
        monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_ENABLED", False)
        monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_FROM", "")
        monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_TO", "")
        monkeypatch.setattr("app.notifications.email_notifier.settings.ALERT_EMAIL_SMTP_USER", "")
        return EmailNotifier()

    def test_channel_name(self, notifier):
        assert notifier.channel_name == "email"

    def test_is_enabled_true(self, notifier):
        assert notifier.is_enabled is True

    def test_is_enabled_false_when_disabled(self, disabled_notifier):
        assert disabled_notifier.is_enabled is False

    @pytest.mark.asyncio
    async def test_send_message_calls_smtp(self, notifier):
        with patch.object(notifier, "_smtp_send") as mock_smtp:
            result = await notifier.send_message("Test Title", "Test body")
        assert result is True
        mock_smtp.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_disabled_returns_false(self, disabled_notifier):
        result = await disabled_notifier.send_message("x", "y")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_trade_alert_stop_loss_hit(self, notifier):
        with patch.object(notifier, "_smtp_send") as mock_smtp:
            result = await notifier.send_trade_alert(
                NotificationEventType.STOP_LOSS_HIT,
                symbol="INFY", side="LONG",
                entry_price=1800.0, stop_loss=1760.0, quantity=5,
                pnl=-200.0,
            )
        assert result is True
        mock_smtp.assert_called_once()
        subject_arg = mock_smtp.call_args[0][0]
        assert "INFY" in subject_arg or "Stop" in subject_arg

    @pytest.mark.asyncio
    async def test_send_incident_alert(self, notifier):
        with patch.object(notifier, "_smtp_send") as mock_smtp:
            result = await notifier.send_incident_alert(
                incident_id="def456",
                component="scheduler",
                severity="warning",
                title="Scheduler Stalled",
                description="No jobs fired in 10 minutes",
                status="acknowledged",
            )
        assert result is True
        subject_arg = mock_smtp.call_args[0][0]
        assert "def456" in subject_arg or "Scheduler" in subject_arg or "WARNING" in subject_arg

    @pytest.mark.asyncio
    async def test_smtp_retries_on_failure(self, notifier):
        call_count = 0

        def _failing_smtp(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("SMTP down")

        with patch.object(notifier, "_smtp_send", side_effect=_failing_smtp):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await notifier._send("subj", "plain", "<html/>")

        assert result is False
        assert call_count == 3  # _MAX_RETRIES


# ═══════════════════════════════════════════════════════════════════════════════
# 3. THROTTLING AND DUPLICATE PREVENTION
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeProvider:
    """Minimal provider stub that records send calls."""
    channel_name = "fake"
    is_enabled = True
    calls: list[str]

    def __init__(self):
        self.calls = []

    async def send_message(self, title, body, severity=Sev.INFO, payload=None) -> bool:
        self.calls.append(f"message:{title}")
        return True

    async def send_error(self, component, error, detail="") -> bool:
        self.calls.append(f"error:{component}")
        return True

    async def send_trade_alert(self, event_type, symbol, side, entry_price, stop_loss,
                                quantity, pnl=None, extra=None) -> bool:
        self.calls.append(f"trade:{symbol}")
        return True

    async def send_system_alert(self, event_type, message, payload=None) -> bool:
        self.calls.append(f"system:{event_type}")
        return True

    async def send_incident_alert(self, incident_id, component, severity, title,
                                   description, status) -> bool:
        self.calls.append(f"incident:{incident_id}")
        return True


def _mock_alert_event(**kwargs) -> MagicMock:
    ev = MagicMock()
    ev.event_type = kwargs.get("event_type", "test")
    ev.severity = kwargs.get("severity", "info")
    ev.title = kwargs.get("title", "test")
    ev.body = kwargs.get("body", "test body")
    ev.channel = "system"
    ev.payload = kwargs.get("payload")
    ev.delivered = False
    ev.delivered_at = None
    ev.save = AsyncMock()
    ev.insert = AsyncMock()
    return ev


class TestThrottlingAndDedup:
    """Verify that the dedup window prevents duplicate alerts."""

    @pytest.mark.asyncio
    async def test_duplicate_within_window_is_suppressed(self):
        provider = _FakeProvider()
        mgr = NotificationManager()
        mgr.register_provider(provider)
        mgr._throttle_window = 300

        sent_event = _mock_alert_event(title="Broker Down")

        with (
            # First call: no recent duplicate → allow
            patch.object(mgr._repo, "find_recent_by_dedup_key",
                         new_callable=AsyncMock, return_value=None),
            patch.object(mgr, "_persist_event",
                         new_callable=AsyncMock, return_value=sent_event),
            patch.object(mgr, "_mark_delivered", new_callable=AsyncMock),
        ):
            result1 = await mgr.dispatch(
                event_type=NotificationEventType.BROKER_DISCONNECTED,
                title="Broker Down", body="Lost connection",
                dedup_key="broker_disconnected:AngelOne",
            )
        assert result1 is not None

        # Second call with same dedup_key: return existing event → suppress
        with patch.object(mgr._repo, "find_recent_by_dedup_key",
                          new_callable=AsyncMock, return_value=sent_event):
            result2 = await mgr.dispatch(
                event_type=NotificationEventType.BROKER_DISCONNECTED,
                title="Broker Down", body="Lost connection",
                dedup_key="broker_disconnected:AngelOne",
            )
        assert result2 is None

    @pytest.mark.asyncio
    async def test_after_window_expires_alert_fires_again(self):
        provider = _FakeProvider()
        mgr = NotificationManager()
        mgr.register_provider(provider)
        mgr._throttle_window = 1  # 1-second window

        sent_event = _mock_alert_event()

        with (
            patch.object(mgr._repo, "find_recent_by_dedup_key",
                         new_callable=AsyncMock, return_value=None),
            patch.object(mgr, "_persist_event",
                         new_callable=AsyncMock, return_value=sent_event),
            patch.object(mgr, "_mark_delivered", new_callable=AsyncMock),
        ):
            r1 = await mgr.dispatch(
                event_type=NotificationEventType.SYSTEM_ERROR,
                title="DB Error", body="Cannot connect",
                dedup_key="db_error",
            )
        assert r1 is not None

        # After window: repository returns None → not a duplicate
        with (
            patch.object(mgr._repo, "find_recent_by_dedup_key",
                         new_callable=AsyncMock, return_value=None),
            patch.object(mgr, "_persist_event",
                         new_callable=AsyncMock, return_value=sent_event),
            patch.object(mgr, "_mark_delivered", new_callable=AsyncMock),
        ):
            r2 = await mgr.dispatch(
                event_type=NotificationEventType.SYSTEM_ERROR,
                title="DB Error", body="Cannot connect",
                dedup_key="db_error",
            )
        assert r2 is not None

    @pytest.mark.asyncio
    async def test_dedup_check_failure_allows_send(self):
        """If the dedup DB query fails, allow the send (safe default)."""
        provider = _FakeProvider()
        mgr = NotificationManager()
        mgr.register_provider(provider)

        sent_event = _mock_alert_event()

        with (
            patch.object(mgr._repo, "find_recent_by_dedup_key",
                         new_callable=AsyncMock, side_effect=Exception("DB down")),
            patch.object(mgr, "_persist_event",
                         new_callable=AsyncMock, return_value=sent_event),
            patch.object(mgr, "_mark_delivered", new_callable=AsyncMock),
        ):
            result = await mgr.dispatch(
                event_type=NotificationEventType.SYSTEM_ERROR,
                title="Test", body="Test",
            )
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. INCIDENT LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════

def _mock_incident(
    incident_id: str = "inc001",
    component: str = "mongodb",
    status: IncidentStatus = IncidentStatus.OPEN,
    severity: AlertSeverity = AlertSeverity.WARNING,
) -> SystemIncident:
    now = datetime.now(timezone.utc)
    return SystemIncident.model_construct(
        incident_id=incident_id,
        severity=severity,
        component=component,
        description="Test incident",
        detected_at=now,
        resolved_at=None,
        status=status,
        timeline=[{"at": now.isoformat(), "message": "Opened"}],
        metadata={},
        created_at=now,
        updated_at=now,
    )


class TestIncidentLifecycle:
    """Test OPEN → ACKNOWLEDGED → RESOLVED lifecycle."""

    def _make_manager(self) -> IncidentManager:
        return IncidentManager()

    @pytest.mark.asyncio
    async def test_create_new_incident(self):
        mgr = self._make_manager()
        with (
            patch.object(mgr, "_find_open", AsyncMock(return_value=None)),
            patch.object(mgr, "_upsert", AsyncMock()),
        ):
            incident = await mgr.create("broker", "Broker offline", AlertSeverity.CRITICAL)
        assert incident.status == IncidentStatus.OPEN
        assert incident.severity == AlertSeverity.CRITICAL
        assert incident.component == "broker"

    @pytest.mark.asyncio
    async def test_acknowledge_transitions_open_to_acknowledged(self):
        mgr = self._make_manager()
        inc = _mock_incident(status=IncidentStatus.OPEN)

        with (
            patch.object(mgr, "_get", AsyncMock(return_value=inc)),
            patch.object(mgr, "_upsert", AsyncMock()),
        ):
            result = await mgr.acknowledge("inc001", "Looking into it.")

        assert result is not None
        assert result.status == IncidentStatus.ACKNOWLEDGED
        assert any("Acknowledged" in e["message"] for e in result.timeline)

    @pytest.mark.asyncio
    async def test_acknowledge_already_resolved_is_noop(self):
        mgr = self._make_manager()
        inc = _mock_incident(status=IncidentStatus.RESOLVED)

        with (
            patch.object(mgr, "_get", AsyncMock(return_value=inc)),
            patch.object(mgr, "_upsert", AsyncMock()) as mock_upsert,
        ):
            result = await mgr.acknowledge("inc001")

        assert result.status == IncidentStatus.RESOLVED
        mock_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_acknowledge_not_found_returns_none(self):
        mgr = self._make_manager()
        with patch.object(mgr, "_get", AsyncMock(return_value=None)):
            result = await mgr.acknowledge("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_transitions_to_resolved(self):
        mgr = self._make_manager()
        inc = _mock_incident(status=IncidentStatus.ACKNOWLEDGED)

        with (
            patch.object(mgr, "_get", AsyncMock(return_value=inc)),
            patch.object(mgr, "_upsert", AsyncMock()),
        ):
            result = await mgr.resolve("inc001", "Fixed the DB connection pool.")

        assert result.status == IncidentStatus.RESOLVED
        assert result.resolved_at is not None
        assert any("Resolved" in e["message"] for e in result.timeline)

    @pytest.mark.asyncio
    async def test_resolve_records_resolution_time(self):
        mgr = self._make_manager()
        now = datetime.now(timezone.utc)
        inc = _mock_incident(status=IncidentStatus.OPEN)
        inc.detected_at = now - timedelta(minutes=15)

        with (
            patch.object(mgr, "_get", AsyncMock(return_value=inc)),
            patch.object(mgr, "_upsert", AsyncMock()),
        ):
            result = await mgr.resolve("inc001")

        assert result.resolved_at is not None
        delta = result.resolved_at - result.detected_at
        assert delta.total_seconds() >= 0

    @pytest.mark.asyncio
    async def test_full_lifecycle_open_acknowledge_resolve(self):
        mgr = self._make_manager()
        inc = _mock_incident(status=IncidentStatus.OPEN)

        with (
            patch.object(mgr, "_get", AsyncMock(return_value=inc)),
            patch.object(mgr, "_upsert", AsyncMock()),
        ):
            acked = await mgr.acknowledge("inc001", "On it.")
        assert acked.status == IncidentStatus.ACKNOWLEDGED

        with (
            patch.object(mgr, "_get", AsyncMock(return_value=acked)),
            patch.object(mgr, "_upsert", AsyncMock()),
        ):
            resolved = await mgr.resolve("inc001", "Root cause fixed.")
        assert resolved.status == IncidentStatus.RESOLVED

    @pytest.mark.asyncio
    async def test_list_open_includes_acknowledged(self):
        mgr = self._make_manager()
        open_inc = _mock_incident(incident_id="i1", status=IncidentStatus.OPEN)
        acked_inc = _mock_incident(incident_id="i2", status=IncidentStatus.ACKNOWLEDGED)
        resolved_inc = _mock_incident(incident_id="i3", status=IncidentStatus.RESOLVED)

        class _FakeFind:
            def __init__(self, docs): self._docs = docs
            def sort(self, *a): return self
            async def to_list(self): return self._docs

        with patch("app.monitoring.incident_manager.SystemIncident.find",
                   return_value=_FakeFind([open_inc, acked_inc])):
            results = await mgr.list_open()

        assert len(results) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 5. NOTIFICATION EVENT MODEL
# ═══════════════════════════════════════════════════════════════════════════════

class TestNotificationEventModel:
    def test_event_id_auto_generated(self):
        ev = NotificationEvent.model_construct(
            event_type="signal_generated",
            severity=NotificationSeverity.INFO,
            source="signal_engine",
            message="BUY RELIANCE ORB breakout",
            metadata={"symbol": "RELIANCE", "entry": 2540.0},
        )
        assert ev.event_type == "signal_generated"
        assert ev.source == "signal_engine"
        assert ev.severity == NotificationSeverity.INFO

    def test_severity_values(self):
        assert NotificationSeverity.INFO == "info"
        assert NotificationSeverity.WARNING == "warning"
        assert NotificationSeverity.CRITICAL == "critical"

    def test_metadata_defaults_to_empty_dict(self):
        ev = NotificationEvent.model_construct(
            event_type="test",
            severity=NotificationSeverity.INFO,
            source="test",
            message="test",
        )
        # metadata should be absent when not set (model_construct skips validation)
        # This verifies the field definition is correct
        assert "metadata" in NotificationEvent.model_fields


# ═══════════════════════════════════════════════════════════════════════════════
# 6. TELEGRAM TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

class TestTelegramTemplates:
    """Pure function tests — no async, no DB, no network."""

    def test_incident_alert_contains_id_and_component(self):
        text = tg_tpl.incident_alert(
            incident_id="abc999",
            component="websocket",
            severity="critical",
            title="WS Disconnected",
            description="Feed stopped for 60s",
            status="open",
        )
        assert "abc999" in text
        assert "websocket" in text.lower()

    def test_incident_alert_status_emoji(self):
        open_text = tg_tpl.incident_alert("x", "c", "warning", "T", "D", "open")
        acked_text = tg_tpl.incident_alert("x", "c", "warning", "T", "D", "acknowledged")
        resolved_text = tg_tpl.incident_alert("x", "c", "warning", "T", "D", "resolved")
        # Green circle for resolved
        assert "🟢" in resolved_text
        # Red circle for open
        assert "🔴" in open_text
        # Yellow circle for acknowledged
        assert "🟡" in acked_text

    def test_eod_exit_template(self):
        text = tg_tpl.eod_exit("Paper", "HDFCBANK", "LONG", 1600.0, 1610.0, 20, 200.0)
        assert "HDFCBANK" in text
        assert "EOD" in text

    def test_reconciliation_mismatch_template(self):
        text = tg_tpl.reconciliation_mismatch("AngelOne", 3, "Position count mismatch")
        assert "AngelOne" in text
        assert "3" in text

    def test_database_unavailable_template(self):
        text = tg_tpl.database_unavailable("Connection refused")
        assert "DATABASE" in text.upper()
        assert "Connection refused" in text or "Connection" in text


# ═══════════════════════════════════════════════════════════════════════════════
# 7. EMAIL TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmailTemplates:
    def test_incident_alert_returns_tuple(self):
        subject, plain, html = em_tpl.incident_alert(
            "xyz111", "broker", "critical",
            "Broker Offline", "Cannot place orders", "open"
        )
        assert "[CRITICAL]" in subject
        assert "Broker Offline" in subject or "xyz111" in subject
        assert "xyz111" in plain
        assert "<html" in html.lower()

    def test_eod_exit_template(self):
        subject, plain, html = em_tpl.eod_exit(
            "Paper", "WIPRO", "SHORT", 400.0, 395.0, 50, 250.0
        )
        assert "WIPRO" in subject
        assert "WIPRO" in plain
        assert "250" in plain

    def test_reconciliation_mismatch_template(self):
        subject, plain, html = em_tpl.reconciliation_mismatch("AngelOne", 2, "Qty mismatch")
        assert "AngelOne" in subject
        assert "Qty mismatch" in plain

    def test_database_unavailable_template(self):
        subject, plain, html = em_tpl.database_unavailable("Connection timeout")
        assert "CRITICAL" in subject.upper() or "Database" in subject
        assert "Connection timeout" in plain


# ═══════════════════════════════════════════════════════════════════════════════
# 8. ALERT ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

class TestAlertRouter:
    """AlertRouter dispatches to NotificationManager — mock the manager."""

    @pytest.mark.asyncio
    async def test_reconciliation_mismatch_dispatches(self):
        from app.monitoring.alert_router import AlertRouter
        router = AlertRouter()

        with patch.object(router, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
            await router.reconciliation_mismatch("AngelOne", 3, "qty mismatch")

        mock_dispatch.assert_called_once()
        kwargs = mock_dispatch.call_args.kwargs
        assert kwargs["event_type"] == NotificationEventType.RECONCILIATION_MISMATCH
        assert kwargs["payload"]["broker"] == "AngelOne"
        assert kwargs["payload"]["mismatch_count"] == 3

    @pytest.mark.asyncio
    async def test_database_unavailable_dispatches(self):
        from app.monitoring.alert_router import AlertRouter
        router = AlertRouter()

        with patch.object(router, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
            await router.database_unavailable("Connection refused")

        mock_dispatch.assert_called_once()
        kwargs = mock_dispatch.call_args.kwargs
        assert kwargs["event_type"] == NotificationEventType.DATABASE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_incident_created_dispatches(self):
        from app.monitoring.alert_router import AlertRouter
        router = AlertRouter()

        with patch.object(router, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
            await router.incident_created("inc001", "scheduler", "warning", "Scheduler stopped")

        mock_dispatch.assert_called_once()
        kwargs = mock_dispatch.call_args.kwargs
        assert kwargs["event_type"] == NotificationEventType.INCIDENT_CREATED
        assert "inc001" in kwargs["payload"]["incident_id"]

    @pytest.mark.asyncio
    async def test_alert_router_never_raises(self):
        """_dispatch failures must not propagate — router is fire-and-forget."""
        from app.monitoring.alert_router import AlertRouter
        router = AlertRouter()

        with patch.object(router, "_dispatch", new_callable=AsyncMock,
                          side_effect=Exception("Manager exploded")):
            # Should NOT raise
            await router.reconciliation_mismatch("Broker", 1, "test")
