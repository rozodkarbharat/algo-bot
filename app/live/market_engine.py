"""
Live market engine — owns the tick stream and feeds the live pipeline.

Responsibilities:
  - Subscribe a watchlist of shortlisted symbols.
  - Accept ticks from a broker-supplied feed (any source: AngelOne WS,
    paper-trade simulator, replay) and route them into the candle builder.
  - Forward closed candles to the signal engine.
  - Track reconnection state and emit a structured status snapshot for ops.

Broker independence:
  - The engine never imports `app.brokers.*`. Ticks reach the engine via the
    `feed_tick()` coroutine; tick sources are wired up by the live signal
    service (which is the layer allowed to bridge broker → engine).

Concurrency:
  - The engine itself is stateless except for the watchlist set; all heavy
    lifting (per-symbol locks, candle aggregation) lives in CandleBuilder.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.live.candle_builder import BuiltCandle, CandleBuilder, Tick
from app.live.market_session import MarketSessionEngine
from app.live.signal_engine import ShortlistedCandidate, SignalEngine
from app.utils.logger import get_logger
from app.utils.market_time import now_utc

logger = get_logger(__name__)


@dataclass
class MarketEngineStats:
    """Operational snapshot of the live market engine."""

    running: bool = False
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    watchlist: list[str] = field(default_factory=list)
    ticks_received: int = 0
    ticks_dropped: int = 0
    candles_emitted: int = 0
    last_tick_at: Optional[datetime] = None
    last_candle_at: Optional[datetime] = None
    reconnect_count: int = 0


class LiveMarketEngine:
    """
    Hub that ties the candle builder and signal engine together.

    The market engine accepts ticks, lets the candle builder aggregate them,
    and re-publishes each closed candle to the signal engine. Both are
    composition fields so callers can inject stubs for tests.
    """

    def __init__(
        self,
        candle_builder: Optional[CandleBuilder] = None,
        signal_engine: Optional[SignalEngine] = None,
        session: Optional[MarketSessionEngine] = None,
    ) -> None:
        self._builder: CandleBuilder = candle_builder or CandleBuilder()
        self._session: MarketSessionEngine = session or MarketSessionEngine()
        self._signals: SignalEngine = signal_engine or SignalEngine(session=self._session)

        # Wire the closed-candle pipeline.
        self._builder.on_candle(self._on_closed_candle)

        self._watchlist: set[str] = set()
        self._running: bool = False
        self._stats: MarketEngineStats = MarketEngineStats()
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Exposed composition ───────────────────────────────────────────────────

    @property
    def candle_builder(self) -> CandleBuilder:
        return self._builder

    @property
    def signal_engine(self) -> SignalEngine:
        return self._signals

    @property
    def session(self) -> MarketSessionEngine:
        return self._session

    @property
    def running(self) -> bool:
        return self._running

    @property
    def watchlist(self) -> list[str]:
        return sorted(self._watchlist)

    @property
    def stats(self) -> MarketEngineStats:
        # Make a fresh snapshot so callers can't mutate internal counters.
        return MarketEngineStats(
            running=self._running,
            started_at=self._stats.started_at,
            stopped_at=self._stats.stopped_at,
            watchlist=self.watchlist,
            ticks_received=self._stats.ticks_received,
            ticks_dropped=self._stats.ticks_dropped,
            candles_emitted=self._stats.candles_emitted,
            last_tick_at=self._stats.last_tick_at,
            last_candle_at=self._stats.last_candle_at,
            reconnect_count=self._stats.reconnect_count,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self, shortlist: list[ShortlistedCandidate]) -> None:
        """
        Boot the engine for a trading session.

        Idempotent: calling start() again with a different shortlist replaces
        the watchlist and reactivates the signal engine for the current day.
        """
        async with self._lock:
            symbols = [c.symbol.upper() for c in shortlist]
            self._watchlist = set(symbols)
            for sym in symbols:
                self._builder.register_symbol(sym)

            trading_date = self._session.current_trading_date()
            self._signals.activate(trading_date=trading_date, shortlist=shortlist)

            self._running = True
            self._stats.started_at = now_utc()
            self._stats.stopped_at = None
            logger.info(
                "LiveMarketEngine started for %s with %d symbols.",
                trading_date, len(symbols),
            )

    async def stop(self) -> None:
        """Shut down signal generation and flush in-progress candles."""
        async with self._lock:
            if not self._running:
                return
            self._signals.deactivate()
            await self._builder.flush_all()
            self._running = False
            self._stats.stopped_at = now_utc()
            logger.info(
                "LiveMarketEngine stopped — ticks=%d, candles=%d, signals=%d.",
                self._stats.ticks_received,
                self._stats.candles_emitted,
                self._signals.stats["signals_emitted"],
            )

    # ── Subscription mutation ─────────────────────────────────────────────────

    def is_subscribed(self, symbol: str) -> bool:
        return symbol.upper() in self._watchlist

    def add_symbol(self, candidate: ShortlistedCandidate) -> None:
        """Add a symbol mid-session (e.g. shortlist refresh). Engine must be running."""
        sym = candidate.symbol.upper()
        if sym in self._watchlist:
            return
        self._watchlist.add(sym)
        self._builder.register_symbol(sym)
        self._signals.add_candidate(candidate)

    def remove_symbol(self, symbol: str) -> None:
        """Drop a symbol from the watchlist; preserves recorded signals."""
        sym = symbol.upper()
        self._watchlist.discard(sym)
        self._builder.unregister_symbol(sym)

    # ── Tick & reconnect API (called by broker bridge in service layer) ──────

    async def feed_tick(self, tick: Tick) -> list[BuiltCandle]:
        """
        Submit a tick to the engine.

        Ticks for symbols outside the watchlist are dropped. Engine must be
        running for ticks to be processed.
        """
        if not self._running:
            return []
        if tick.symbol.upper() not in self._watchlist:
            self._stats.ticks_dropped += 1
            return []

        emitted = await self._builder.on_tick(tick)
        self._stats.ticks_received += 1
        self._stats.last_tick_at = tick.timestamp
        return emitted

    async def note_reconnect(self) -> None:
        """
        Inform the engine that the broker feed reconnected.

        Stale in-progress buckets are intentionally NOT flushed — the candle
        builder's gap-detection rolls them forward on the next tick. We only
        bump the counter so ops can correlate disconnects with signal gaps.
        """
        self._stats.reconnect_count += 1
        logger.warning("Tick feed reconnect noted (count=%d).", self._stats.reconnect_count)

    # ── Internal pipeline ─────────────────────────────────────────────────────

    async def _on_closed_candle(self, candle: BuiltCandle) -> None:
        """Pipe each closed candle into the signal engine."""
        self._stats.candles_emitted += 1
        self._stats.last_candle_at = candle.end_time
        await self._signals.on_candle(candle)


# ── Module-level singleton (used by the service & scheduler) ─────────────────

live_market_engine: LiveMarketEngine = LiveMarketEngine()
