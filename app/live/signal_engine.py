"""
Live signal engine — ORB breakout detection and LiveSignal generation.

Receives closed 15-min candles from the live market engine and:
  1. Captures the first 15-min candle (09:15–09:30 IST) as the opening range.
  2. Skips symbols whose ORB range exceeds the configured maximum.
  3. On any subsequent 15-min candle CLOSE breaking the ORB high → BUY signal.
     On any close breaking the ORB low → SELL signal.
  4. Enforces a single trade per (symbol, trading_date) via in-memory locks
     and the unique index on LiveSignal.

The engine is broker-independent and does NOT persist anything itself — it
emits a `GeneratedSignal` to its registered callback. The live signal service
is responsible for persistence + WebSocket broadcasting.

Look-ahead safety:
  - Decisions are made strictly on CLOSED candles.
  - The first 15-min candle is captured only once it closes at 09:30 IST
    (caller passes built candles in chronological close order).

Multi-strategy support:
  - The engine accepts a `Shortlist` mapping (symbol → ShortlistedCandidate)
    so future strategies can attach extra context (probability, direction
    prior, etc.) without changing the engine signature.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Awaitable, Callable, Optional

from app.config.settings import settings
from app.live.candle_builder import BuiltCandle
from app.live.market_session import (
    FIRST_CANDLE_CLOSE,
    FIRST_CANDLE_OPEN,
    LATEST_ENTRY_TIME,
    MarketSessionEngine,
)
from app.models.live_signal import LiveBreakoutSide, LiveSignalType
from app.utils.candle_intervals import CandleInterval
from app.utils.logger import get_logger
from app.utils.market_time import IST, to_ist

logger = get_logger(__name__)


# ── Inputs / outputs ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ShortlistedCandidate:
    """
    A symbol the engine is allowed to signal on for a trading day.

    Sourced from the research/shortlist service. The optional `direction_hint`
    is the historical bias from yesterday's one-side day; it is currently used
    for diagnostics only — the strategy still emits on whichever side actually
    breaks today.
    """

    symbol: str
    probability: Optional[float] = None
    direction_hint: Optional[str] = None  # "UP" | "DOWN" | None


@dataclass
class _SymbolState:
    """Per-symbol in-memory state for the live session."""

    symbol: str
    trading_date: date
    candidate: ShortlistedCandidate

    first_candle: Optional[BuiltCandle] = None
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    orb_range_percent: Optional[float] = None
    orb_skipped_reason: Optional[str] = None  # e.g. "range>1%"

    trade_locked: bool = False
    signal_emitted_at: Optional[datetime] = None


@dataclass(frozen=True)
class GeneratedSignal:
    """A breakout signal produced by the engine — not persisted by the engine."""

    symbol: str
    trading_date: date
    signal_type: LiveSignalType
    breakout_side: LiveBreakoutSide
    entry_price: float
    stop_loss: float
    first_candle_high: float
    first_candle_low: float
    orb_range_percent: float
    breakout_time: datetime  # UTC
    probability_score: Optional[float] = None
    # ── Multi-strategy identity ───────────────────────────────────────────────
    strategy_id: str = "one_side_orb"
    strategy_name: str = "One-Side ORB"
    strategy_version: str = "1.0.0"
    metadata: dict = field(default_factory=dict)


SignalCallback = Callable[[GeneratedSignal], Awaitable[None]]


# ── Engine ───────────────────────────────────────────────────────────────────

class SignalEngine:
    """
    Pure live-signal engine — consumes BuiltCandle events, emits GeneratedSignal.

    Lifecycle:
      engine = SignalEngine(session)
      engine.on_signal(callback)
      engine.activate(trading_date, shortlist)
      ...
      await engine.on_candle(candle)
      ...
      engine.deactivate()

    Concurrency:
      - One asyncio.Lock guards mutations of the per-symbol state map.
      - Callback dispatch happens outside the lock.
    """

    def __init__(
        self,
        session: Optional[MarketSessionEngine] = None,
        max_orb_range_percent: Optional[float] = None,
        first_candle_interval: CandleInterval = CandleInterval.FIFTEEN_MINUTE,
        strategy_id: str = "one_side_orb",
        strategy_name: str = "One-Side ORB",
        strategy_version: str = "1.0.0",
    ) -> None:
        self._session: MarketSessionEngine = session or MarketSessionEngine()
        self._max_orb_range: float = (
            max_orb_range_percent
            if max_orb_range_percent is not None
            else settings.LIVE_MAX_ORB_RANGE_PCT
        )
        self._interval: CandleInterval = first_candle_interval
        self._strategy_id: str = strategy_id
        self._strategy_name: str = strategy_name
        self._strategy_version: str = strategy_version

        self._active: bool = False
        self._trading_date: Optional[date] = None
        self._states: dict[str, _SymbolState] = {}
        self._callbacks: list[SignalCallback] = []
        self._lock: asyncio.Lock = asyncio.Lock()

        self._signals_emitted: int = 0
        self._candles_seen: int = 0

    # ── Subscription API ──────────────────────────────────────────────────────

    def on_signal(self, callback: SignalCallback) -> None:
        """Register an async callback fired on every newly generated signal."""
        self._callbacks.append(callback)

    # ── Activation ────────────────────────────────────────────────────────────

    def activate(
        self,
        trading_date: date,
        shortlist: list[ShortlistedCandidate],
    ) -> None:
        """
        Prepare the engine for a new trading session.

        Wipes any prior state and seeds an empty state row per shortlisted
        symbol. Idempotent — calling twice with the same args is safe.
        """
        self._trading_date = trading_date
        self._states = {
            c.symbol.upper(): _SymbolState(
                symbol=c.symbol.upper(),
                trading_date=trading_date,
                candidate=c,
            )
            for c in shortlist
        }
        self._active = True
        logger.info(
            "SignalEngine activated for %s with %d candidates.",
            trading_date, len(self._states),
        )

    def deactivate(self) -> None:
        """Stop signal generation. State is preserved for diagnostics."""
        self._active = False
        logger.info(
            "SignalEngine deactivated (signals_emitted=%d).", self._signals_emitted
        )

    def add_candidate(self, candidate: ShortlistedCandidate) -> None:
        """
        Mid-session injection of a shortlisted candidate.

        No-op if the engine is inactive or the symbol is already tracked.
        Keeps state construction inside the engine so private invariants
        remain encapsulated.
        """
        if not self._active or self._trading_date is None:
            return
        sym = candidate.symbol.upper()
        if sym in self._states:
            return
        self._states[sym] = _SymbolState(
            symbol=sym,
            trading_date=self._trading_date,
            candidate=candidate,
        )

    @property
    def active(self) -> bool:
        return self._active

    @property
    def stats(self) -> dict:
        locked = sum(1 for s in self._states.values() if s.trade_locked)
        captured = sum(1 for s in self._states.values() if s.first_candle is not None)
        skipped = sum(1 for s in self._states.values() if s.orb_skipped_reason)
        return {
            "active": self._active,
            "trading_date": self._trading_date.isoformat() if self._trading_date else None,
            "shortlisted": len(self._states),
            "first_candle_captured": captured,
            "orb_skipped": skipped,
            "trade_locked": locked,
            "candles_seen": self._candles_seen,
            "signals_emitted": self._signals_emitted,
        }

    def get_symbol_state(self, symbol: str) -> Optional[_SymbolState]:
        """Return a snapshot of the per-symbol state (read-only)."""
        return self._states.get(symbol.upper())

    # ── Candle ingestion ──────────────────────────────────────────────────────

    async def on_candle(self, candle: BuiltCandle) -> Optional[GeneratedSignal]:
        """
        Feed a CLOSED candle to the engine. Returns the emitted signal, if any.

        The engine ignores:
          - intervals other than the configured first-candle interval (default 15m)
          - candles for symbols not on today's shortlist
          - candles outside the entry window for breakout detection
          - any candle when the engine is inactive
        """
        if not self._active:
            return None

        if candle.interval is not self._interval:
            return None

        symbol = candle.symbol.upper()
        state = self._states.get(symbol)
        if state is None:
            # Not a shortlisted symbol — silently ignore.
            return None

        self._candles_seen += 1
        signal: Optional[GeneratedSignal] = None

        async with self._lock:
            candle_ist = to_ist(candle.start_time).time()
            close_ist = to_ist(candle.end_time).time()

            # ── Capture the opening range from the first 15-min candle ────────
            if state.first_candle is None:
                if candle_ist == FIRST_CANDLE_OPEN and close_ist == FIRST_CANDLE_CLOSE:
                    state.first_candle = candle
                    state.orb_high = candle.high
                    state.orb_low = candle.low
                    state.orb_range_percent = candle.range_percent

                    if state.orb_range_percent > self._max_orb_range:
                        state.orb_skipped_reason = (
                            f"orb_range_percent={state.orb_range_percent:.2f}% "
                            f"> max {self._max_orb_range:.2f}%"
                        )
                        logger.info(
                            "[%s] ORB skipped — %s", symbol, state.orb_skipped_reason
                        )
                return None  # Either captured or this isn't the ORB candle yet.

            if state.trade_locked:
                return None

            if state.orb_skipped_reason is not None:
                return None

            # ── Entry window guard ────────────────────────────────────────────
            # We use the candle's OPEN time so the 09:30 candle (the first
            # eligible breakout candle) qualifies, and 11:30 is exclusive
            # (matches the documented "9:30 AM → 11:30 AM IST" window).
            if not (FIRST_CANDLE_CLOSE <= candle_ist < LATEST_ENTRY_TIME):
                return None

            # ── Breakout test — strict CLOSE comparison ───────────────────────
            assert state.orb_high is not None and state.orb_low is not None
            if candle.close > state.orb_high:
                signal = self._build_signal(
                    state=state,
                    candle=candle,
                    side=LiveBreakoutSide.UP,
                )
            elif candle.close < state.orb_low:
                signal = self._build_signal(
                    state=state,
                    candle=candle,
                    side=LiveBreakoutSide.DOWN,
                )

            if signal is not None:
                state.trade_locked = True
                state.signal_emitted_at = candle.end_time
                self._signals_emitted += 1

        if signal is not None:
            await self._dispatch(signal)
        return signal

    # ── External lock control ────────────────────────────────────────────────

    def lock_symbol(self, symbol: str) -> None:
        """
        Hard-lock a symbol — no more signals will be emitted for it.

        Called by the live signal service after persistence succeeds, so a
        broker-side acknowledgement of a duplicate (or any external
        invalidation) propagates back into the engine immediately.
        """
        s = self._states.get(symbol.upper())
        if s is not None:
            s.trade_locked = True

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_signal(
        self,
        state: _SymbolState,
        candle: BuiltCandle,
        side: LiveBreakoutSide,
    ) -> GeneratedSignal:
        assert state.orb_high is not None and state.orb_low is not None
        assert state.orb_range_percent is not None
        assert self._trading_date is not None

        if side is LiveBreakoutSide.UP:
            signal_type = LiveSignalType.BUY
            stop_loss = state.orb_low
        else:
            signal_type = LiveSignalType.SELL
            stop_loss = state.orb_high

        return GeneratedSignal(
            symbol=state.symbol,
            trading_date=self._trading_date,
            signal_type=signal_type,
            breakout_side=side,
            entry_price=candle.close,
            stop_loss=stop_loss,
            first_candle_high=state.orb_high,
            first_candle_low=state.orb_low,
            orb_range_percent=state.orb_range_percent,
            breakout_time=candle.end_time,
            probability_score=state.candidate.probability,
            strategy_id=self._strategy_id,
            strategy_name=self._strategy_name,
            strategy_version=self._strategy_version,
            metadata={
                "direction_hint": state.candidate.direction_hint,
                "candle_open": candle.open,
                "candle_high": candle.high,
                "candle_low": candle.low,
                "candle_volume": candle.volume,
            },
        )

    async def _dispatch(self, signal: GeneratedSignal) -> None:
        for cb in self._callbacks:
            try:
                await cb(signal)
            except Exception as exc:
                logger.error(
                    "Signal callback raised for %s: %s",
                    signal.symbol, exc, exc_info=True,
                )
