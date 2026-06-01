"""
Alert router — bridges monitoring events into the notification system.

Routes health-check failures and risk breaches to
`notification_manager.dispatch_system_alert()` using the correct event
type, severity, and dedup key so alerts fire once per failure window
rather than on every check cycle.

This module does NOT modify the notification system; it only calls its
public dispatch methods.
"""

from __future__ import annotations

from typing import Optional

from app.models.alert_event import AlertSeverity
from app.notifications.base_notifier import NotificationEventType
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AlertRouter:
    """
    Routes monitoring events to the notification manager.

    All methods are fire-and-forget (best-effort, never raise).
    """

    # ── Infrastructure alerts ─────────────────────────────────────────────────

    async def broker_disconnected(self, broker: str, reason: str = "") -> None:
        await self._dispatch(
            event_type=NotificationEventType.BROKER_DISCONNECTED,
            message=f"Broker {broker} is disconnected. {reason}".strip(),
            severity=AlertSeverity.CRITICAL,
            payload={"broker": broker, "reason": reason},
            dedup_key=f"broker_disconnected:{broker}",
        )

    async def scheduler_stopped(self, job_id: Optional[str] = None) -> None:
        msg = "APScheduler has stopped." if not job_id else f"Scheduler job failed: {job_id}"
        await self._dispatch(
            event_type=NotificationEventType.SCHEDULER_FAILURE,
            message=msg,
            severity=AlertSeverity.CRITICAL,
            payload={"job_id": job_id},
            dedup_key=f"scheduler_stopped:{job_id or 'global'}",
        )

    async def database_unreachable(self, error: str = "") -> None:
        await self._dispatch(
            event_type=NotificationEventType.SYSTEM_ERROR,
            message=f"MongoDB is unreachable. {error}".strip(),
            severity=AlertSeverity.CRITICAL,
            payload={"component": "mongodb", "error": error},
            dedup_key="database_unreachable",
        )

    async def database_unavailable(self, error: str = "") -> None:
        """Explicit DATABASE_UNAVAILABLE event (distinct from generic SYSTEM_ERROR)."""
        await self._dispatch(
            event_type=NotificationEventType.DATABASE_UNAVAILABLE,
            message=f"MongoDB is unavailable. {error}".strip(),
            severity=AlertSeverity.CRITICAL,
            payload={"component": "mongodb", "error": error},
            dedup_key="database_unavailable",
        )

    async def reconciliation_mismatch(
        self,
        broker: str,
        mismatch_count: int,
        description: str = "",
    ) -> None:
        """Fire when a broker reconciliation run finds position discrepancies."""
        await self._dispatch(
            event_type=NotificationEventType.RECONCILIATION_MISMATCH,
            message=(
                f"Reconciliation mismatch with {broker}: {mismatch_count} discrepancies. "
                f"{description}".strip()
            ),
            severity=AlertSeverity.WARNING,
            payload={
                "broker": broker,
                "mismatch_count": mismatch_count,
                "description": description,
            },
            dedup_key=f"reconciliation_mismatch:{broker}",
        )

    async def incident_created(
        self,
        incident_id: str,
        component: str,
        severity: str,
        description: str,
    ) -> None:
        """Notify operators when a new incident is opened."""
        await self._dispatch(
            event_type=NotificationEventType.INCIDENT_CREATED,
            message=f"New {severity} incident opened for {component}: {description}",
            severity=AlertSeverity(severity) if severity in AlertSeverity._value2member_map_ else AlertSeverity.WARNING,
            payload={
                "incident_id": incident_id,
                "component": component,
                "description": description,
            },
            dedup_key=f"incident_created:{incident_id}",
        )

    # ── Market data alerts ────────────────────────────────────────────────────

    async def market_data_stale(
        self, seconds_since_last_tick: float, symbol: Optional[str] = None
    ) -> None:
        target = symbol or "feed"
        await self._dispatch(
            event_type=NotificationEventType.SYSTEM_ERROR,
            message=f"Market data stale: {target} — no ticks for {seconds_since_last_tick:.0f}s.",
            severity=AlertSeverity.WARNING,
            payload={"target": target, "age_seconds": seconds_since_last_tick},
            dedup_key=f"market_data_stale:{target}",
        )

    async def no_signals_today(self, symbols_checked: int) -> None:
        await self._dispatch(
            event_type=NotificationEventType.SYSTEM_ERROR,
            message=f"No trading signals generated today. {symbols_checked} symbols scanned.",
            severity=AlertSeverity.WARNING,
            payload={"symbols_checked": symbols_checked},
            dedup_key="no_signals_today",
        )

    # ── Execution alerts ──────────────────────────────────────────────────────

    async def high_rejection_rate(
        self, rejection_rate: float, total_orders: int
    ) -> None:
        await self._dispatch(
            event_type=NotificationEventType.SYSTEM_ERROR,
            message=(
                f"High order rejection rate: {rejection_rate*100:.0f}% "
                f"({total_orders} orders today)."
            ),
            severity=AlertSeverity.WARNING,
            payload={"rejection_rate": rejection_rate, "total_orders": total_orders},
            dedup_key="high_rejection_rate",
        )

    async def kill_switch_engaged(self, reason: str) -> None:
        await self._dispatch(
            event_type=NotificationEventType.SYSTEM_ERROR,
            message=f"Live execution kill switch engaged: {reason}",
            severity=AlertSeverity.CRITICAL,
            payload={"reason": reason},
            dedup_key="kill_switch_engaged",
        )

    # ── Risk alerts ───────────────────────────────────────────────────────────

    async def daily_loss_limit_breached(
        self, current_loss: float, limit: float
    ) -> None:
        await self._dispatch(
            event_type=NotificationEventType.SYSTEM_ERROR,
            message=(
                f"Daily loss limit breached: ₹{abs(current_loss):,.2f} "
                f"(limit ₹{limit:,.2f}). Portfolio halted."
            ),
            severity=AlertSeverity.CRITICAL,
            payload={"current_loss": current_loss, "limit": limit},
            dedup_key="daily_loss_limit_breached",
        )

    async def exposure_limit_warning(
        self, utilization_pct: float, limit_pct: float
    ) -> None:
        await self._dispatch(
            event_type=NotificationEventType.SYSTEM_ERROR,
            message=(
                f"Portfolio exposure near limit: {utilization_pct:.1f}% "
                f"(warning at {limit_pct:.1f}%)."
            ),
            severity=AlertSeverity.WARNING,
            payload={"utilization_pct": utilization_pct, "limit_pct": limit_pct},
            dedup_key="portfolio_exposure_warning",
        )

    async def strategy_concentration_warning(
        self, strategy_id: str, pct: float, limit_pct: float
    ) -> None:
        await self._dispatch(
            event_type=NotificationEventType.SYSTEM_ERROR,
            message=(
                f"Strategy {strategy_id} concentration high: {pct:.1f}% of capital "
                f"(limit {limit_pct:.1f}%)."
            ),
            severity=AlertSeverity.WARNING,
            payload={"strategy_id": strategy_id, "pct": pct, "limit_pct": limit_pct},
            dedup_key=f"strategy_concentration:{strategy_id}",
        )

    async def sector_concentration_warning(
        self, sector: str, pct: float, limit_pct: float
    ) -> None:
        await self._dispatch(
            event_type=NotificationEventType.SYSTEM_ERROR,
            message=(
                f"Sector '{sector}' concentration high: {pct:.1f}% of capital "
                f"(limit {limit_pct:.1f}%)."
            ),
            severity=AlertSeverity.WARNING,
            payload={"sector": sector, "pct": pct, "limit_pct": limit_pct},
            dedup_key=f"sector_concentration:{sector}",
        )

    # ── Incident escalation ───────────────────────────────────────────────────

    async def escalation_alert(
        self, component: str, incident_id: str, reason: str
    ) -> None:
        """Immediate escalation notification — bypasses normal dedup window."""
        try:
            from app.notifications.notification_manager import notification_manager
            await notification_manager.dispatch_system_alert(
                event_type=NotificationEventType.SYSTEM_ERROR,
                message=(
                    f"ESCALATION — {component}: {reason} (incident {incident_id})"
                ),
                severity=AlertSeverity.CRITICAL,
                payload={"component": component, "incident_id": incident_id, "reason": reason},
                # Unique dedup key per escalation event prevents suppression
                dedup_key=f"escalation:{incident_id}:{reason[:20]}",
            )
        except Exception as exc:
            logger.error("[alert-router] escalation alert failed: %s", exc)

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _dispatch(
        self,
        event_type: str,
        message: str,
        severity: AlertSeverity,
        payload: Optional[dict] = None,
        dedup_key: Optional[str] = None,
    ) -> None:
        try:
            from app.notifications.notification_manager import notification_manager
            await notification_manager.dispatch_system_alert(
                event_type=event_type,
                message=message,
                severity=severity,
                payload=payload,
                dedup_key=dedup_key,
            )
        except Exception as exc:
            logger.error("[alert-router] dispatch failed [%s]: %s", event_type, exc)


# ── Module-level singleton ────────────────────────────────────────────────────

alert_router = AlertRouter()
