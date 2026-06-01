"""
NotificationManager — provider registry, routing, throttling, and dedup.

The manager holds an ordered list of BaseNotifier providers. When a send
request arrives it:

  1. Checks the dedup window (AlertEventRepository.find_recent_by_dedup_key)
     — suppresses burst re-fires of the same event within the cooldown window.
  2. Persists an AlertEvent document for audit and WebSocket broadcast.
  3. Dispatches to the first enabled provider (primary) and optionally all
     enabled providers when `broadcast_all=True`.

Provider priority:
  - Telegram (if enabled) has priority for real-time alerts
  - Email (if enabled) is the fallback / secondary channel
  - System (log-only) is always active and used when no external channel fires

The manager is instantiated as a module-level singleton (`notification_manager`)
so services can import and use it without re-creating the provider list.

Adding a future provider (WhatsApp, Slack …):
  1. Implement BaseNotifier
  2. Instantiate the class and call notification_manager.register_provider()
     — or add it to _build_default_providers() below
"""

from datetime import datetime, timezone
from typing import Any, Optional

from app.config.settings import settings
from app.models.alert_event import AlertChannel, AlertEvent, AlertSeverity
from app.notifications.base_notifier import BaseNotifier, NotificationEventType
from app.repositories.alert_event_repository import AlertEventRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _default_dedup_key(event_type: str, symbol: Optional[str] = None) -> str:
    if symbol:
        return f"{event_type}:{symbol}"
    return event_type


class NotificationManager:
    """
    Manages the notification provider registry and dispatches alerts.

    Usage:
        await notification_manager.dispatch(
            event_type=NotificationEventType.SIGNAL_GENERATED,
            title="BUY Signal: RELIANCE",
            body="ORB breakout at ₹2540",
            severity=AlertSeverity.INFO,
        )
    """

    def __init__(self) -> None:
        self._providers: list[BaseNotifier] = []
        self._repo = AlertEventRepository()
        self._throttle_window = settings.NOTIFY_THROTTLE_WINDOW_SECONDS

    # ── Provider registration ─────────────────────────────────────────────────

    def register_provider(self, provider: BaseNotifier) -> None:
        """Add a notification provider to the registry."""
        self._providers.append(provider)
        logger.debug("Registered notification provider: %s", provider.channel_name)

    def get_providers(self) -> list[BaseNotifier]:
        return list(self._providers)

    @property
    def active_providers(self) -> list[BaseNotifier]:
        return [p for p in self._providers if p.is_enabled]

    # ── Core dispatch ─────────────────────────────────────────────────────────

    async def dispatch(
        self,
        event_type: NotificationEventType,
        title: str,
        body: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        payload: Optional[dict[str, Any]] = None,
        dedup_key: Optional[str] = None,
        broadcast_all: bool = False,
    ) -> AlertEvent | None:
        """
        Dispatch an alert through registered providers.

        Returns the persisted AlertEvent, or None if suppressed by dedup.
        Never raises.
        """
        try:
            dk = dedup_key or event_type
            if await self._is_duplicate(dk):
                logger.debug("Notification suppressed (dedup): %s", event_type)
                return None

            channel = self._choose_channel()
            event = await self._persist_event(event_type, title, body, severity, payload, dk, channel)

            await self._dispatch_to_providers(event, broadcast_all)
            return event

        except Exception as exc:
            logger.error("NotificationManager.dispatch failed [%s]: %s", event_type, exc)
            return None

    # ── Typed dispatch helpers (used by NotificationService) ─────────────────

    async def dispatch_trade_alert(
        self,
        event_type: NotificationEventType,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        quantity: int,
        pnl: Optional[float] = None,
        extra: Optional[dict[str, Any]] = None,
        dedup_key: Optional[str] = None,
    ) -> AlertEvent | None:
        """Route a trade lifecycle event through all enabled providers."""
        extra = extra or {}
        dk = dedup_key or _default_dedup_key(event_type, symbol)

        if await self._is_duplicate(dk):
            logger.debug("Trade alert suppressed (dedup): %s:%s", event_type, symbol)
            return None

        pnl_str = f" | P&L ₹{pnl:+.2f}" if pnl is not None else ""
        title = f"{event_type.replace('_', ' ').title()}: {symbol}"
        body = f"{side} | Entry ₹{entry_price:.2f} | SL ₹{stop_loss:.2f}{pnl_str}"
        severity = (
            AlertSeverity.WARNING
            if event_type == NotificationEventType.STOP_LOSS_HIT
            else AlertSeverity.INFO
        )

        channel = self._choose_channel()
        event = await self._persist_event(event_type, title, body, severity, {
            "symbol": symbol, "side": side, "entry_price": entry_price,
            "stop_loss": stop_loss, "quantity": quantity, "pnl": pnl, **(extra or {}),
        }, dk, channel)

        for provider in self.active_providers:
            try:
                success = await provider.send_trade_alert(
                    event_type, symbol, side, entry_price, stop_loss, quantity, pnl, extra
                )
                if success:
                    await self._mark_delivered(event)
                    break
            except Exception as exc:
                logger.error("Provider %s failed: %s", provider.channel_name, exc)

        return event

    async def dispatch_system_alert(
        self,
        event_type: NotificationEventType,
        message: str,
        payload: Optional[dict[str, Any]] = None,
        severity: AlertSeverity = AlertSeverity.CRITICAL,
        dedup_key: Optional[str] = None,
    ) -> AlertEvent | None:
        """Route a system/infrastructure alert."""
        payload = payload or {}
        dk = dedup_key or _default_dedup_key(event_type, payload.get("component") or payload.get("broker"))

        if await self._is_duplicate(dk):
            logger.debug("System alert suppressed (dedup): %s", event_type)
            return None

        channel = self._choose_channel()
        event = await self._persist_event(event_type, str(event_type), message, severity, payload, dk, channel)

        for provider in self.active_providers:
            try:
                success = await provider.send_system_alert(event_type, message, payload)
                if success:
                    await self._mark_delivered(event)
                    break
            except Exception as exc:
                logger.error("Provider %s failed: %s", provider.channel_name, exc)

        return event

    async def dispatch_daily_summary(
        self,
        summary_data: dict[str, Any],
    ) -> AlertEvent | None:
        """Broadcast the daily summary through all enabled providers."""
        from app.notifications.templates import telegram_templates as tg_tpl
        from app.notifications.templates import email_templates as em_tpl
        from app.notifications.telegram_notifier import TelegramNotifier
        from app.notifications.email_notifier import EmailNotifier

        dk = f"daily_summary:{summary_data.get('trading_date', 'unknown')}"

        # Daily summary is NOT deduplicated — only sent once per day at 3:45 PM
        # so we skip the dedup check for this event type.

        channel = self._choose_channel()
        body = (
            f"Trades: {summary_data.get('total_trades', 0)} | "
            f"Win Rate: {summary_data.get('win_rate', 0.0):.1f}% | "
            f"P&L: ₹{summary_data.get('total_pnl', 0.0):+,.2f}"
        )
        event = await self._persist_event(
            NotificationEventType.DAILY_SUMMARY,
            f"Daily Summary — {summary_data.get('trading_date', 'unknown')}",
            body,
            AlertSeverity.INFO,
            summary_data,
            dk,
            channel,
        )

        for provider in self.active_providers:
            try:
                if isinstance(provider, TelegramNotifier):
                    text = tg_tpl.daily_summary(**summary_data)  # type: ignore[arg-type]
                    success = await provider._send(text)
                elif isinstance(provider, EmailNotifier):
                    subject, plain, html = em_tpl.daily_summary(**summary_data)  # type: ignore[arg-type]
                    success = await provider._send(subject, plain, html)
                else:
                    success = await provider.send_message(
                        f"Daily Summary — {summary_data.get('trading_date')}",
                        body,
                        AlertSeverity.INFO,
                        summary_data,
                    )
                if success:
                    await self._mark_delivered(event)
            except Exception as exc:
                logger.error(
                    "Provider %s failed on daily summary: %s", provider.channel_name, exc
                )

        return event

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _is_duplicate(self, dedup_key: str) -> bool:
        try:
            existing = await self._repo.find_recent_by_dedup_key(
                dedup_key, self._throttle_window
            )
            return existing is not None
        except Exception as exc:
            logger.warning("Dedup check failed (allowing send): %s", exc)
            return False

    def _choose_channel(self) -> AlertChannel:
        for p in self._providers:
            if p.is_enabled:
                try:
                    return AlertChannel(p.channel_name)
                except ValueError:
                    return AlertChannel.SYSTEM
        return AlertChannel.SYSTEM

    async def _persist_event(
        self,
        event_type: str,
        title: str,
        body: str,
        severity: AlertSeverity,
        payload: Optional[dict[str, Any]],
        dedup_key: str,
        channel: AlertChannel,
    ) -> AlertEvent:
        event = AlertEvent(
            event_type=str(event_type),
            severity=severity,
            title=title,
            body=body,
            payload=payload,
            channel=channel,
            dedup_key=dedup_key,
        )
        await event.insert()
        return event

    async def _mark_delivered(self, event: AlertEvent) -> None:
        try:
            event.delivered = True
            event.delivered_at = datetime.now(timezone.utc)
            await event.save()
        except Exception as exc:
            logger.warning("Failed to mark alert delivered: %s", exc)

    async def _dispatch_to_providers(
        self, event: AlertEvent, broadcast_all: bool
    ) -> None:
        delivered = False
        for provider in self.active_providers:
            try:
                success = await provider.send_message(
                    event.title, event.body, AlertSeverity(event.severity), event.payload
                )
                if success:
                    await self._mark_delivered(event)
                    delivered = True
                    if not broadcast_all:
                        break
            except Exception as exc:
                logger.error("Provider %s dispatch error: %s", provider.channel_name, exc)

        if not delivered and not self.active_providers:
            # System-only: log the alert even without external providers
            logger.info(
                "Alert [%s] %s: %s (system-only — no external providers enabled)",
                event.severity.upper(), event.event_type, event.title,
            )


def _build_default_providers() -> list[BaseNotifier]:
    """
    Construct the default provider list.

    Order defines dispatch priority: first enabled provider wins for
    single-channel sends (broadcast_all=False).

    To add a new provider (e.g. Slack), append it here.
    """
    from app.notifications.telegram_notifier import TelegramNotifier
    from app.notifications.email_notifier import EmailNotifier

    return [TelegramNotifier(), EmailNotifier()]


# ── Module-level singleton ─────────────────────────────────────────────────────

notification_manager = NotificationManager()

# Register default providers at module load time.
for _p in _build_default_providers():
    notification_manager.register_provider(_p)
