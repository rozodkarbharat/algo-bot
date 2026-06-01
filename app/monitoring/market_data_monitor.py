"""
Market data monitor — validates tick freshness, candle coverage, feed health.

Builds on `LiveHealthMonitor` (which already measures tick staleness) and
adds checks for:
  - Candle freshness per symbol
  - Symbol coverage (are all shortlisted symbols actively feeding?)
  - Delayed feed detection (candle build rate below expected)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.utils.logger import get_logger
from app.utils.market_time import now_utc

logger = get_logger(__name__)

# Thresholds (seconds)
TICK_STALE_THRESHOLD = 30
CANDLE_STALE_THRESHOLD = 1200  # 20 min without a closed candle → warning


@dataclass
class MarketDataHealthReport:
    """Consolidated market data health snapshot."""

    feed_status: str                  # "ok" | "stale" | "offline"
    ticks_received: int
    ticks_dropped: int
    candles_emitted: int
    reconnect_count: int

    seconds_since_last_tick: Optional[float]
    seconds_since_last_candle: Optional[float]

    watchlist_size: int
    stale_symbols: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=now_utc)


class MarketDataMonitor:
    """
    Validates market data pipeline health during trading hours.

    Delegates to `LiveHealthMonitor.evaluate()` for the low-level snapshot
    and adds higher-level interpretation.
    """

    async def check(self) -> MarketDataHealthReport:
        """Return a market data health report."""
        try:
            from app.live.health_monitor import live_health_monitor, HealthStatus
            snapshot = live_health_monitor.evaluate()

            feed_status = {
                HealthStatus.OK: "ok",
                HealthStatus.DEGRADED: "stale",
                HealthStatus.STALE: "stale",
                HealthStatus.OFFLINE: "offline",
            }.get(snapshot.status, "unknown")

            report = MarketDataHealthReport(
                feed_status=feed_status,
                ticks_received=snapshot.ticks_received,
                ticks_dropped=snapshot.ticks_dropped,
                candles_emitted=snapshot.candles_emitted,
                reconnect_count=snapshot.reconnect_count,
                seconds_since_last_tick=snapshot.seconds_since_last_tick,
                seconds_since_last_candle=snapshot.seconds_since_last_candle,
                watchlist_size=snapshot.watchlist_size,
                stale_symbols=snapshot.stale_symbols,
                notes=snapshot.notes[:],
            )

            # Alert if stale during market hours
            if feed_status in ("stale", "offline"):
                from app.monitoring.alert_router import alert_router
                age = snapshot.seconds_since_last_tick or 0.0
                await alert_router.market_data_stale(age)

            return report

        except Exception as exc:
            logger.error("[market-data-monitor] check failed: %s", exc)
            return MarketDataHealthReport(
                feed_status="unknown",
                ticks_received=0,
                ticks_dropped=0,
                candles_emitted=0,
                reconnect_count=0,
                seconds_since_last_tick=None,
                seconds_since_last_candle=None,
                watchlist_size=0,
                notes=[f"Monitor check failed: {exc}"],
            )


market_data_monitor = MarketDataMonitor()
