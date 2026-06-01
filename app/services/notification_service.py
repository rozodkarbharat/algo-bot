"""
NotificationService — high-level event dispatcher for trading operations.

This service is the single entry point for the rest of the application to
send notifications. It:

  1. Applies category-level enable/disable checks from settings
     (NOTIFY_TRADE_ALERTS, NOTIFY_SIGNAL_ALERTS, NOTIFY_SYSTEM_ALERTS, etc.)
  2. Delegates to NotificationManager for provider routing, dedup, and
     persistence (AlertEvent documents).
  3. Broadcasts notification events to WebSocket subscribers in the
     "notifications" room so the React dashboard gets real-time alerts.

Usage from any service or scheduler job:
    from app.services.notification_service import notification_service

    await notification_service.on_signal_generated(
        symbol="RELIANCE", side="BUY", entry=2540.0, sl=2490.0, prob=0.71
    )

Architecture note:
  - This service has NO knowledge of providers. All provider logic lives in
    app/notifications/.
  - Services that already use alert_service.py can continue to do so —
    both coexist. New code should prefer notification_service for full
    category control and WebSocket integration.
"""

import asyncio
from typing import Any, Optional

from app.config.settings import settings
from app.models.alert_event import AlertSeverity
from app.notifications.base_notifier import NotificationEventType
from app.notifications.notification_manager import notification_manager
from app.utils.logger import get_logger
from app.websocket.manager import ws_manager

logger = get_logger(__name__)

# WebSocket room that receives all notification events.
_NOTIFICATIONS_ROOM = "notifications"


class NotificationService:
    """
    Domain-aware notification dispatcher.

    All public methods are fire-and-forget coroutines — they never raise.
    """

    # ── Signal events ─────────────────────────────────────────────────────────

    async def on_signal_generated(
        self,
        symbol: str,
        side: str,
        entry: float,
        sl: float,
        prob: Optional[float] = None,
        orb_range_pct: Optional[float] = None,
    ) -> None:
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_SIGNAL_ALERTS):
            return
        try:
            event = await notification_manager.dispatch(
                event_type=NotificationEventType.SIGNAL_GENERATED,
                title=f"{side} Signal: {symbol}",
                body=f"Entry ₹{entry:.2f} | SL ₹{sl:.2f}" + (
                    f" | Prob {prob * 100:.1f}%" if prob is not None else ""
                ),
                severity=AlertSeverity.INFO,
                payload={
                    "symbol": symbol, "side": side, "entry": entry,
                    "sl": sl, "prob": prob, "orb_range_pct": orb_range_pct,
                },
                dedup_key=f"signal_generated:{symbol}:{side}",
            )
            if event:
                await self._broadcast_ws(event_type=NotificationEventType.SIGNAL_GENERATED,
                                         symbol=symbol, side=side, entry=entry, sl=sl, prob=prob)
        except Exception as exc:
            logger.error("on_signal_generated failed: %s", exc)

    # ── Paper trade events ────────────────────────────────────────────────────

    async def on_paper_trade_entered(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        quantity: int,
        trading_date: str = "",
    ) -> None:
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_TRADE_ALERTS):
            return
        await self._dispatch_trade(
            NotificationEventType.PAPER_TRADE_ENTERED,
            symbol, side, entry_price, stop_loss, quantity,
            extra={"mode": "Paper", "trading_date": trading_date},
            dedup_key=f"paper_trade_entered:{symbol}",
        )

    async def on_paper_trade_exited(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        exit_price: float,
        quantity: int,
        pnl: float,
        exit_reason: str,
        trading_date: str = "",
    ) -> None:
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_TRADE_ALERTS):
            return
        await self._dispatch_trade(
            NotificationEventType.PAPER_TRADE_EXITED,
            symbol, side, entry_price, stop_loss, quantity, pnl=pnl,
            extra={
                "mode": "Paper", "exit_price": exit_price,
                "exit_reason": exit_reason, "trading_date": trading_date,
            },
            # Exit events are not deduplicated — each exit is unique.
            dedup_key=None,
        )

    async def on_stop_loss_hit(
        self,
        symbol: str,
        side: str,
        stop_loss: float,
        entry_price: float,
        quantity: int,
        pnl: float,
        mode: str = "Paper",
    ) -> None:
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_TRADE_ALERTS):
            return
        await self._dispatch_trade(
            NotificationEventType.STOP_LOSS_HIT,
            symbol, side, entry_price, stop_loss, quantity, pnl=pnl,
            extra={"mode": mode},
            dedup_key=f"sl_hit:{symbol}",
        )

    # ── Live trade events ─────────────────────────────────────────────────────

    async def on_live_trade_entered(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        quantity: int,
        trading_date: str = "",
    ) -> None:
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_TRADE_ALERTS):
            return
        await self._dispatch_trade(
            NotificationEventType.LIVE_TRADE_ENTERED,
            symbol, side, entry_price, stop_loss, quantity,
            extra={"mode": "Live", "trading_date": trading_date},
            dedup_key=f"live_trade_entered:{symbol}",
        )

    async def on_live_trade_exited(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        exit_price: float,
        quantity: int,
        pnl: float,
        exit_reason: str,
        trading_date: str = "",
    ) -> None:
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_TRADE_ALERTS):
            return
        await self._dispatch_trade(
            NotificationEventType.LIVE_TRADE_EXITED,
            symbol, side, entry_price, stop_loss, quantity, pnl=pnl,
            extra={
                "mode": "Live", "exit_price": exit_price,
                "exit_reason": exit_reason, "trading_date": trading_date,
            },
            dedup_key=None,
        )

    # ── Infrastructure / system events ────────────────────────────────────────

    async def on_broker_disconnected(self, broker: str, reason: str) -> None:
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_SYSTEM_ALERTS):
            return
        try:
            event = await notification_manager.dispatch_system_alert(
                event_type=NotificationEventType.BROKER_DISCONNECTED,
                message=f"Lost connection to {broker}. Reason: {reason}",
                payload={"broker": broker, "reason": reason},
                severity=AlertSeverity.CRITICAL,
                dedup_key=f"broker_disconnected:{broker}",
            )
            if event:
                await self._broadcast_ws_system(
                    NotificationEventType.BROKER_DISCONNECTED, event.body,
                    {"broker": broker, "reason": reason},
                )
        except Exception as exc:
            logger.error("on_broker_disconnected failed: %s", exc)

    async def on_websocket_disconnected(self, feed: str, reason: str) -> None:
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_SYSTEM_ALERTS):
            return
        try:
            await notification_manager.dispatch_system_alert(
                event_type=NotificationEventType.WEBSOCKET_DISCONNECTED,
                message=f"WebSocket feed '{feed}' disconnected: {reason}",
                payload={"feed": feed, "reason": reason},
                severity=AlertSeverity.WARNING,
                dedup_key=f"ws_disconnected:{feed}",
            )
        except Exception as exc:
            logger.error("on_websocket_disconnected failed: %s", exc)

    async def on_scheduler_failure(self, job_id: str, error: str) -> None:
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_SYSTEM_ALERTS):
            return
        try:
            event = await notification_manager.dispatch_system_alert(
                event_type=NotificationEventType.SCHEDULER_FAILURE,
                message=f"Scheduler job '{job_id}' failed: {error}",
                payload={"job_id": job_id, "error": error},
                severity=AlertSeverity.CRITICAL,
                dedup_key=f"scheduler_failure:{job_id}",
            )
            if event:
                await self._broadcast_ws_system(
                    NotificationEventType.SCHEDULER_FAILURE, event.body,
                    {"job_id": job_id, "error": error},
                )
        except Exception as exc:
            logger.error("on_scheduler_failure failed: %s", exc)

    async def on_system_error(
        self, component: str, error: str, detail: str = ""
    ) -> None:
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_SYSTEM_ALERTS):
            return
        try:
            event = await notification_manager.dispatch_system_alert(
                event_type=NotificationEventType.SYSTEM_ERROR,
                message=error,
                payload={"component": component, "error": error, "detail": detail},
                severity=AlertSeverity.CRITICAL,
                dedup_key=f"system_error:{component}",
            )
            if event:
                await self._broadcast_ws_system(
                    NotificationEventType.SYSTEM_ERROR, error,
                    {"component": component},
                )
        except Exception as exc:
            logger.error("on_system_error failed: %s", exc)

    async def on_eod_exit(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        pnl: float,
        mode: str = "Paper",
        trading_date: str = "",
    ) -> None:
        """EOD forced exit — all positions closed at market close."""
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_TRADE_ALERTS):
            return
        await self._dispatch_trade(
            NotificationEventType.EOD_EXIT,
            symbol, side, entry_price, entry_price, quantity, pnl=pnl,
            extra={
                "mode": mode, "exit_price": exit_price,
                "exit_reason": "EOD_EXIT", "trading_date": trading_date,
            },
            dedup_key=None,
        )

    async def on_reconciliation_mismatch(
        self,
        broker: str,
        mismatch_count: int,
        description: str = "",
    ) -> None:
        """Broker position reconciliation found discrepancies."""
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_SYSTEM_ALERTS):
            return
        try:
            event = await notification_manager.dispatch_system_alert(
                event_type=NotificationEventType.RECONCILIATION_MISMATCH,
                message=f"Reconciliation mismatch with {broker}: {mismatch_count} discrepancies.",
                payload={
                    "broker": broker,
                    "mismatch_count": mismatch_count,
                    "description": description,
                },
                severity=AlertSeverity.WARNING,
                dedup_key=f"reconciliation_mismatch:{broker}",
            )
            if event:
                await self._broadcast_ws_system(
                    NotificationEventType.RECONCILIATION_MISMATCH,
                    event.body,
                    {"broker": broker, "mismatch_count": mismatch_count},
                )
        except Exception as exc:
            logger.error("on_reconciliation_mismatch failed: %s", exc)

    async def on_database_unavailable(self, error: str = "") -> None:
        """MongoDB is unreachable — all trading suspended."""
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_SYSTEM_ALERTS):
            return
        try:
            event = await notification_manager.dispatch_system_alert(
                event_type=NotificationEventType.DATABASE_UNAVAILABLE,
                message=f"MongoDB is unavailable: {error}",
                payload={"component": "mongodb", "error": error},
                severity=AlertSeverity.CRITICAL,
                dedup_key="database_unavailable",
            )
            if event:
                await self._broadcast_ws_system(
                    NotificationEventType.DATABASE_UNAVAILABLE,
                    event.body,
                    {"component": "mongodb"},
                )
        except Exception as exc:
            logger.error("on_database_unavailable failed: %s", exc)

    async def on_incident_created(
        self,
        incident_id: str,
        component: str,
        severity: str,
        title: str,
        description: str,
        status: str = "open",
    ) -> None:
        """Dispatch a notification when a new system incident is opened."""
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_SYSTEM_ALERTS):
            return
        try:
            for provider in notification_manager.active_providers:
                try:
                    await provider.send_incident_alert(
                        incident_id=incident_id,
                        component=component,
                        severity=severity,
                        title=title,
                        description=description,
                        status=status,
                    )
                except Exception as exc:
                    logger.error(
                        "send_incident_alert failed for provider %s: %s",
                        provider.channel_name, exc,
                    )
            await self._broadcast_ws_system(
                NotificationEventType.INCIDENT_CREATED,
                f"Incident opened: {title}",
                {"incident_id": incident_id, "component": component, "severity": severity},
            )
        except Exception as exc:
            logger.error("on_incident_created failed: %s", exc)

    # ── Daily summary ─────────────────────────────────────────────────────────

    async def send_daily_summary(self, mode: str = "Paper") -> None:
        if not (settings.NOTIFY_ENABLED and settings.NOTIFY_DAILY_SUMMARY):
            logger.info("Daily summary disabled via settings")
            return
        try:
            from app.notifications.daily_summary import build_daily_summary
            summary = await build_daily_summary(mode=mode)
            event = await notification_manager.dispatch_daily_summary(summary)
            if event:
                await ws_manager.broadcast_to_room(
                    {"type": "daily_summary", "data": summary},
                    _NOTIFICATIONS_ROOM,
                )
            logger.info("Daily summary dispatched for %s", summary.get("trading_date"))
        except Exception as exc:
            logger.error("send_daily_summary failed: %s", exc)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _dispatch_trade(
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
    ) -> None:
        try:
            event = await notification_manager.dispatch_trade_alert(
                event_type=event_type,
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                stop_loss=stop_loss,
                quantity=quantity,
                pnl=pnl,
                extra=extra,
                dedup_key=dedup_key,
            )
            if event:
                await self._broadcast_ws_trade(event_type, symbol, side, entry_price, stop_loss, quantity, pnl, extra)
        except Exception as exc:
            logger.error("_dispatch_trade [%s:%s] failed: %s", event_type, symbol, exc)

    async def _broadcast_ws(self, **kwargs: Any) -> None:
        try:
            await ws_manager.broadcast_to_room(
                {"type": "notification", **kwargs},
                _NOTIFICATIONS_ROOM,
            )
        except Exception as exc:
            logger.warning("WS broadcast failed: %s", exc)

    async def _broadcast_ws_trade(
        self,
        event_type: NotificationEventType,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        quantity: int,
        pnl: Optional[float],
        extra: Optional[dict[str, Any]],
    ) -> None:
        try:
            await ws_manager.broadcast_to_room(
                {
                    "type": "trade_notification",
                    "event_type": str(event_type),
                    "symbol": symbol,
                    "side": side,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "quantity": quantity,
                    "pnl": pnl,
                    **(extra or {}),
                },
                _NOTIFICATIONS_ROOM,
            )
        except Exception as exc:
            logger.warning("WS trade broadcast failed: %s", exc)

    async def _broadcast_ws_system(
        self,
        event_type: NotificationEventType,
        message: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        try:
            await ws_manager.broadcast_to_room(
                {
                    "type": "system_notification",
                    "event_type": str(event_type),
                    "message": message,
                    **(payload or {}),
                },
                _NOTIFICATIONS_ROOM,
            )
        except Exception as exc:
            logger.warning("WS system broadcast failed: %s", exc)


# Module-level singleton — import this in services, scheduler jobs, etc.
notification_service = NotificationService()
