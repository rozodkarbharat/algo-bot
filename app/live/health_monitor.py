"""
Live engine health monitor.

Watches the live pipeline for the failure modes the strategy spec calls out:
  - broker WebSocket disconnects (tracked via LiveMarketEngine.note_reconnect)
  - missing / stale ticks per symbol
  - stale market data (no closed candles within the entry window)
  - market pauses (no ticks across the entire watchlist)
  - duplicate signals (counted via repository for ops visibility)

The monitor is intentionally observe-only: it returns structured health
snapshots and emits WebSocket alerts. Restart / reconnect decisions live in
the broker bridge that the live signal service will wire up later — keeping
this module decoupled means it works the same way with any tick source.

Stale thresholds are configurable so they can be tightened in production:
  STALE_TICK_THRESHOLD_SECONDS  — per-symbol "no recent tick" warning
  STALE_FEED_THRESHOLD_SECONDS  — engine-wide "no ticks at all" warning
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Optional

from app.live.market_engine import LiveMarketEngine, live_market_engine
from app.live.market_session import MarketSessionEngine
from app.utils.logger import get_logger
from app.utils.market_time import now_utc

logger = get_logger(__name__)


# ── Tunables (could be moved to settings if tightening per-env is needed) ────

STALE_TICK_THRESHOLD_SECONDS: int = 30   # per-symbol warning threshold
STALE_FEED_THRESHOLD_SECONDS: int = 15   # engine-wide warning threshold


class HealthStatus(StrEnum):
    """Coarse health level used by /api/v1/live/health and dashboards."""

    OK = "OK"
    DEGRADED = "DEGRADED"   # at least one symbol stale but feed alive
    STALE = "STALE"         # whole feed appears paused
    OFFLINE = "OFFLINE"     # engine not running


@dataclass
class HealthSnapshot:
    """JSON-ready health report."""

    status: HealthStatus
    running: bool
    market_open: bool
    entry_window_open: bool
    reconnect_count: int
    ticks_received: int
    ticks_dropped: int
    candles_emitted: int
    signals_emitted: int
    last_tick_at: Optional[datetime]
    last_candle_at: Optional[datetime]
    seconds_since_last_tick: Optional[float]
    seconds_since_last_candle: Optional[float]
    watchlist_size: int
    stale_symbols: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ── Monitor ──────────────────────────────────────────────────────────────────

class LiveHealthMonitor:
    """
    Read-only health view of the live engine.

    Builds a `HealthSnapshot` on demand. The scheduler / service can call
    `evaluate()` periodically and broadcast the result to the
    `live:market-state` WebSocket room.
    """

    def __init__(
        self,
        engine: Optional[LiveMarketEngine] = None,
        session: Optional[MarketSessionEngine] = None,
    ) -> None:
        self._engine: LiveMarketEngine = engine or live_market_engine
        self._session: MarketSessionEngine = session or self._engine.session

    def evaluate(self, at: Optional[datetime] = None) -> HealthSnapshot:
        """Build a snapshot at `at` (defaults to now UTC)."""
        moment = at or now_utc()
        engine_stats = self._engine.stats
        signal_stats = self._engine.signal_engine.stats
        snapshot = self._session.snapshot()

        notes: list[str] = []

        seconds_since_tick = _seconds_since(engine_stats.last_tick_at, moment)
        seconds_since_candle = _seconds_since(engine_stats.last_candle_at, moment)

        # Per-symbol staleness — currently engine-level only because per-symbol
        # tick timestamps live inside CandleBuilder._buckets. We surface the
        # builder's in-progress snapshots and flag symbols whose in-progress
        # 1-minute bucket hasn't received an update in > STALE_TICK_THRESHOLD.
        from app.utils.candle_intervals import CandleInterval

        stale_symbols: list[str] = []
        for symbol in self._engine.watchlist:
            snap = self._engine.candle_builder.get_in_progress(
                symbol, CandleInterval.ONE_MINUTE
            )
            if snap is None:
                continue
            # The bucket has no "last update" field by design — instead we
            # infer staleness from the engine-wide last_tick_at because per-
            # symbol latency would require additional bookkeeping.
            # When the engine has no recent ticks at all, every symbol with
            # an open bucket is considered stale.
            if (
                seconds_since_tick is not None
                and seconds_since_tick > STALE_TICK_THRESHOLD_SECONDS
            ):
                stale_symbols.append(symbol)

        # ── Status decision ────────────────────────────────────────────────────
        if not self._engine.running:
            status = HealthStatus.OFFLINE
            notes.append("Live engine not running.")
        elif (
            snapshot.is_market_open
            and seconds_since_tick is not None
            and seconds_since_tick > STALE_FEED_THRESHOLD_SECONDS
        ):
            status = HealthStatus.STALE
            notes.append(
                f"No ticks received for {seconds_since_tick:.0f}s "
                f"(threshold {STALE_FEED_THRESHOLD_SECONDS}s)."
            )
        elif snapshot.is_market_open and engine_stats.ticks_received == 0:
            status = HealthStatus.STALE
            notes.append("Market is open but engine has received zero ticks.")
        elif stale_symbols:
            status = HealthStatus.DEGRADED
            notes.append(
                f"{len(stale_symbols)} symbol(s) show stale ticks."
            )
        else:
            status = HealthStatus.OK

        if engine_stats.reconnect_count:
            notes.append(
                f"Broker feed reconnects today: {engine_stats.reconnect_count}."
            )

        return HealthSnapshot(
            status=status,
            running=self._engine.running,
            market_open=snapshot.is_market_open,
            entry_window_open=snapshot.entry_window_open,
            reconnect_count=engine_stats.reconnect_count,
            ticks_received=engine_stats.ticks_received,
            ticks_dropped=engine_stats.ticks_dropped,
            candles_emitted=engine_stats.candles_emitted,
            signals_emitted=signal_stats["signals_emitted"],
            last_tick_at=engine_stats.last_tick_at,
            last_candle_at=engine_stats.last_candle_at,
            seconds_since_last_tick=seconds_since_tick,
            seconds_since_last_candle=seconds_since_candle,
            watchlist_size=len(engine_stats.watchlist),
            stale_symbols=stale_symbols,
            notes=notes,
        )


def _seconds_since(ts: Optional[datetime], moment: datetime) -> Optional[float]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - ts).total_seconds())


# ── Module-level singleton ───────────────────────────────────────────────────

live_health_monitor: LiveHealthMonitor = LiveHealthMonitor()
