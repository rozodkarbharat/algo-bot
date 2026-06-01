"""
Abstract provider interface for all notification channels.

Every notification channel (Telegram, Email, WhatsApp, Slack …) implements
BaseNotifier. The NotificationManager holds a registry of enabled providers
and dispatches through this interface, keeping the call-site unaware of
which concrete transport is in use.

All send methods return True on success and False on failure — they never
raise exceptions to the caller, matching the best-effort contract of the
overall alerting system.
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, Optional


class NotificationEventType(StrEnum):
    """
    Canonical set of domain events that can trigger a notification.

    Values are used as `event_type` keys stored in AlertEvent documents
    and transmitted over WebSocket.
    """

    # Trading signal events
    SIGNAL_GENERATED = "signal_generated"

    # Paper trade lifecycle
    PAPER_TRADE_ENTERED = "paper_trade_entered"
    PAPER_TRADE_EXITED = "paper_trade_exited"

    # Live trade lifecycle
    LIVE_TRADE_ENTERED = "live_trade_entered"
    LIVE_TRADE_EXITED = "live_trade_exited"

    # Risk events
    STOP_LOSS_HIT = "stop_loss_hit"

    # Trade exit reason
    EOD_EXIT = "eod_exit"

    # Summary
    DAILY_SUMMARY = "daily_summary"

    # Infrastructure failures
    BROKER_DISCONNECTED = "broker_disconnected"
    WEBSOCKET_DISCONNECTED = "websocket_disconnected"
    SCHEDULER_FAILURE = "scheduler_failure"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"
    DATABASE_UNAVAILABLE = "database_unavailable"
    SYSTEM_ERROR = "system_error"

    # Incident lifecycle
    INCIDENT_CREATED = "incident_created"
    INCIDENT_ESCALATED = "incident_escalated"


class AlertSeverity(StrEnum):
    """Severity level for a notification."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class BaseNotifier(ABC):
    """
    Abstract base class for all notification providers.

    Concrete implementations must override `channel_name`, `is_enabled`,
    and all four abstract `send_*` methods.

    All `send_*` methods are async, accept keyword-only contextual data,
    and return True on successful delivery, False otherwise. They must
    never raise exceptions — catch and log internally.
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Human-readable name for this channel (e.g. 'telegram', 'email')."""

    @property
    @abstractmethod
    def is_enabled(self) -> bool:
        """Return True when the provider is configured and active."""

    # ── Core send methods ─────────────────────────────────────────────────────

    @abstractmethod
    async def send_message(
        self,
        title: str,
        body: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Send a free-form notification.

        Used for messages that don't fit a specific domain template.
        """

    @abstractmethod
    async def send_error(
        self,
        component: str,
        error: str,
        detail: str = "",
    ) -> bool:
        """
        Send an error alert for a system component.

        Args:
            component: Name of the subsystem that failed (e.g. 'APScheduler').
            error:     Short error message.
            detail:    Optional additional context (traceback excerpt, etc.).
        """

    @abstractmethod
    async def send_trade_alert(
        self,
        event_type: NotificationEventType,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        quantity: int,
        pnl: Optional[float] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Send a trade lifecycle alert (entry, exit, SL hit).

        Args:
            event_type:  One of the PAPER_TRADE_* / LIVE_TRADE_* / STOP_LOSS_HIT events.
            symbol:      NSE ticker.
            side:        'LONG' or 'SHORT'.
            entry_price: Trade entry price (₹).
            stop_loss:   Stop loss price (₹).
            quantity:    Number of shares.
            pnl:         Net P&L (₹) — None for entry events.
            extra:       Any additional context dict.
        """

    @abstractmethod
    async def send_system_alert(
        self,
        event_type: NotificationEventType,
        message: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Send an infrastructure / system alert.

        Args:
            event_type: One of the infrastructure event types.
            message:    Human-readable alert message.
            payload:    Structured data for dashboards or logging.
        """

    async def send_incident_alert(
        self,
        incident_id: str,
        component: str,
        severity: str,
        title: str,
        description: str,
        status: str,
    ) -> bool:
        """
        Send an incident lifecycle alert.

        Default implementation delegates to send_message() so providers that
        haven't overridden this method still deliver the alert via generic text.
        Override in each provider for a richer, incident-specific template.

        Args:
            incident_id:  Short hex ID of the incident.
            component:    Affected system component.
            severity:     'info' | 'warning' | 'critical'.
            title:        One-line incident summary.
            description:  Full incident description.
            status:       Current lifecycle status (open/acknowledged/resolved).
        """
        body = f"[{severity.upper()}] {component} — {description} (ID: {incident_id})"
        return await self.send_message(
            title=title,
            body=body,
            severity=AlertSeverity(severity) if severity in AlertSeverity._value2member_map_ else AlertSeverity.WARNING,
        )
