"""Live signal/market engine health check."""

from __future__ import annotations

import time

from app.monitoring.health_checks.base import BaseHealthCheck, ComponentHealthResult
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SignalEngineHealthCheck(BaseHealthCheck):
    """
    Check the live market engine: running state, watchlist, tick/candle stats.

    Delegates to `LiveHealthMonitor` which already computes a detailed
    snapshot — we just translate its `HealthStatus` into our vocabulary.
    """

    @property
    def component_name(self) -> str:
        return "signal_engine"

    async def _run(self) -> ComponentHealthResult:
        t0 = time.perf_counter()
        try:
            from app.live.health_monitor import live_health_monitor, HealthStatus
            snapshot = live_health_monitor.evaluate()
            latency_ms = (time.perf_counter() - t0) * 1000

            meta = {
                "running": snapshot.running,
                "market_open": snapshot.market_open,
                "ticks_received": snapshot.ticks_received,
                "ticks_dropped": snapshot.ticks_dropped,
                "candles_emitted": snapshot.candles_emitted,
                "signals_emitted": snapshot.signals_emitted,
                "reconnect_count": snapshot.reconnect_count,
                "watchlist_size": snapshot.watchlist_size,
                "stale_symbols": snapshot.stale_symbols,
                "notes": snapshot.notes,
            }

            if snapshot.status == HealthStatus.OFFLINE:
                return ComponentHealthResult.unhealthy(
                    self.component_name,
                    message="Live signal engine is not running.",
                    latency_ms=latency_ms,
                    **meta,
                )
            elif snapshot.status == HealthStatus.STALE:
                return ComponentHealthResult.degraded(
                    self.component_name,
                    latency_ms=latency_ms,
                    message="; ".join(snapshot.notes) or "Feed is stale.",
                    **meta,
                )
            elif snapshot.status == HealthStatus.DEGRADED:
                return ComponentHealthResult.degraded(
                    self.component_name,
                    latency_ms=latency_ms,
                    message="; ".join(snapshot.notes) or "Some symbols stale.",
                    **meta,
                )
            return ComponentHealthResult.ok(self.component_name, latency_ms=latency_ms, **meta)

        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("[monitor:signal-engine] check failed: %s", exc)
            return ComponentHealthResult.unhealthy(
                self.component_name,
                message=f"Signal engine check failed: {exc}",
                latency_ms=latency_ms,
            )
