"""
ORHV Phase 3 — Live Signal Generator.

Pure Python — NO database calls, NO I/O.
Receives closed 15-min candles for Day D+1 and emits a signal when the
first valid ORH/ORL breakout occurs within the configured time window.

Differences from One-Side ORB's SignalEngine:
  - Direction is NOT pre-determined: whichever side breaks first wins.
  - Entry time cutoff is 12:00 IST (vs 11:30 IST for One-Side ORB).
  - Range filter: ORB range must be ≤ 1% of OR_Close.

Usage (live integration):
    The ORHVLiveEngine wraps this generator and wires it into the existing
    MarketEngine candle stream.  The shortlist for a given day is built from
    yesterday's ORHVValidationRecord documents (tradable=True).

Concurrency:
  - This class is stateful per trading day (one instance per session).
  - External locking is the caller's responsibility if multiple threads feed candles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from app.models.historical_candle import CandleData
from app.strategy.strategies.opening_range_historical_validation.config import ORHVConfig
from app.strategy.strategies.opening_range_historical_validation.constants import (
    EOD_EXIT_UTC_HOUR,
    EOD_EXIT_UTC_MINUTE,
    MAX_ENTRY_UTC_HOUR,
    MAX_ENTRY_UTC_MINUTE,
    ORB_CLOSE_UTC_HOUR,
    ORB_CLOSE_UTC_MINUTE,
    ORB_OPEN_UTC_HOUR,
    ORB_OPEN_UTC_MINUTE,
    STRATEGY_ID,
    STRATEGY_NAME,
    STRATEGY_VERSION,
)
from app.utils.logger import get_logger
from app.utils.market_time import to_ist

logger = get_logger(__name__)


# ── Value objects ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ORHVCandidate:
    """
    A symbol validated by Phase 2 and ready for Phase 3 live monitoring.

    The win_rate and occurrences_used are sourced from the most recent
    ORHVValidationRecord for this symbol.
    """

    symbol: str
    win_rate: float
    occurrences_used: int
    candidate_date: date             # Day D (when the setup was detected)
    orh_d: Optional[float] = None   # Day D ORH (informational)
    orl_d: Optional[float] = None   # Day D ORL (informational)


@dataclass
class ORHVSignalEvent:
    """
    A Phase 3 live signal ready for persistence and broadcast.

    Modelled after GeneratedSignal from app/live/signal_engine.py so it can
    be adapted to the standard live signal pipeline.
    """

    symbol: str
    trading_date: date              # Day D+1
    candidate_date: date            # Day D
    signal_type: str                # "BUY" | "SELL"

    entry_price: float              # ORH (BUY) or ORL (SELL) of Day D+1
    stop_loss: float                # ORL (BUY) or ORH (SELL) of Day D+1
    orh: float
    orl: float
    or_close: float
    orb_range_pct: float
    breakout_time: datetime         # UTC

    win_rate: float
    occurrences_used: int

    strategy_id: str = STRATEGY_ID
    strategy_name: str = STRATEGY_NAME
    strategy_version: str = STRATEGY_VERSION

    metadata: dict = field(default_factory=dict)


# ── Per-symbol intraday state ─────────────────────────────────────────────────

@dataclass
class _ORHVSymbolState:
    symbol: str
    candidate: ORHVCandidate

    first_candle: Optional[CandleData] = None
    orh: Optional[float] = None
    orl: Optional[float] = None
    or_close: Optional[float] = None
    orb_range_pct: Optional[float] = None
    range_rejected: bool = False       # True if ORB range > max
    trade_locked: bool = False         # True after a signal is emitted
    signal_emitted_at: Optional[datetime] = None


# ── Signal generator ──────────────────────────────────────────────────────────

class ORHVSignalGenerator:
    """
    Stateful Phase 3 live signal generator for one trading session.

    Lifecycle:
        gen = ORHVSignalGenerator(config)
        gen.activate(trading_date, candidates)
        ...
        signal = gen.on_candle(candle)   # may return ORHVSignalEvent
        ...
        gen.deactivate()
    """

    def __init__(self, config: Optional[ORHVConfig] = None) -> None:
        self._cfg = config or ORHVConfig()
        self._active = False
        self._trading_date: Optional[date] = None
        self._states: dict[str, _ORHVSymbolState] = {}
        self._signals_emitted = 0
        self._candles_seen = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def activate(
        self,
        trading_date: date,
        candidates: list[ORHVCandidate],
    ) -> None:
        """Prepare generator for a new session.  Resets all per-symbol state."""
        self._trading_date = trading_date
        self._states = {
            c.symbol.upper(): _ORHVSymbolState(symbol=c.symbol.upper(), candidate=c)
            for c in candidates
        }
        self._active = True
        self._signals_emitted = 0
        self._candles_seen = 0
        logger.info(
            "ORHVSignalGenerator activated for %s with %d candidates.",
            trading_date, len(self._states),
        )

    def deactivate(self) -> None:
        self._active = False
        logger.info(
            "ORHVSignalGenerator deactivated (signals=%d).", self._signals_emitted
        )

    def add_candidate(self, candidate: ORHVCandidate) -> None:
        """Mid-session injection — no-op if engine is inactive or symbol already tracked."""
        if not self._active or self._trading_date is None:
            return
        sym = candidate.symbol.upper()
        if sym not in self._states:
            self._states[sym] = _ORHVSymbolState(symbol=sym, candidate=candidate)

    def lock_symbol(self, symbol: str) -> None:
        """Hard-lock a symbol so no further signals are emitted for it today."""
        state = self._states.get(symbol.upper())
        if state:
            state.trade_locked = True

    @property
    def active(self) -> bool:
        return self._active

    @property
    def stats(self) -> dict:
        return {
            "active": self._active,
            "trading_date": self._trading_date.isoformat() if self._trading_date else None,
            "candidates": len(self._states),
            "first_candle_captured": sum(1 for s in self._states.values() if s.first_candle),
            "range_rejected": sum(1 for s in self._states.values() if s.range_rejected),
            "trade_locked": sum(1 for s in self._states.values() if s.trade_locked),
            "candles_seen": self._candles_seen,
            "signals_emitted": self._signals_emitted,
        }

    # ── Candle ingestion ──────────────────────────────────────────────────────

    def on_candle(self, candle: CandleData) -> Optional[ORHVSignalEvent]:
        """
        Feed a CLOSED 15-min candle.  Returns ORHVSignalEvent if a signal fires.

        This is a synchronous method (no async/await) — the caller wraps it in
        asyncio.get_event_loop().run_in_executor() if needed.
        """
        if not self._active or self._trading_date is None:
            return None

        symbol = getattr(candle, "symbol", "").upper()
        state = self._states.get(symbol)
        if state is None:
            return None

        self._candles_seen += 1

        candle_ist = to_ist(candle.time).time()

        # ── Capture Opening Range from the first 9:15 candle ─────────────────
        if state.first_candle is None:
            import datetime as dt_mod
            open_ist = dt_mod.time(9, 15)
            close_ist = dt_mod.time(9, 30)
            if candle_ist == open_ist or (
                candle.time.hour == ORB_OPEN_UTC_HOUR
                and candle.time.minute == ORB_OPEN_UTC_MINUTE
            ):
                state.first_candle = candle
                state.orh = candle.high
                state.orl = candle.low
                state.or_close = candle.close

                if state.or_close and state.or_close > 0:
                    state.orb_range_pct = (candle.high - candle.low) / state.or_close * 100.0
                else:
                    state.orb_range_pct = 0.0

                if state.orb_range_pct > self._cfg.max_orb_range_pct:
                    state.range_rejected = True
                    logger.info(
                        "[ORHV] %s: ORB range %.2f%% > %.2f%% — rejected.",
                        symbol, state.orb_range_pct, self._cfg.max_orb_range_pct,
                    )
            return None

        # ── Guards ────────────────────────────────────────────────────────────
        if state.trade_locked or state.range_rejected:
            return None

        # ── Entry window check ────────────────────────────────────────────────
        if not self._in_entry_window(candle):
            return None

        assert state.orh is not None and state.orl is not None

        # ── First breakout → signal (touch-based: high/low trades through ORH/ORL) ─
        signal_type: Optional[str] = None
        if candle.high > state.orh:
            signal_type = "BUY"
        elif candle.low < state.orl:
            signal_type = "SELL"

        if signal_type is None:
            return None

        state.trade_locked = True
        state.signal_emitted_at = candle.time
        self._signals_emitted += 1

        entry_price = state.orh if signal_type == "BUY" else state.orl
        stop_loss = state.orl if signal_type == "BUY" else state.orh

        event = ORHVSignalEvent(
            symbol=state.symbol,
            trading_date=self._trading_date,
            candidate_date=state.candidate.candidate_date,
            signal_type=signal_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            orh=state.orh,
            orl=state.orl,
            or_close=state.or_close or state.orh,
            orb_range_pct=round(state.orb_range_pct or 0.0, 4),
            breakout_time=candle.time,
            win_rate=state.candidate.win_rate,
            occurrences_used=state.candidate.occurrences_used,
            metadata={
                "candle_close": candle.close,
                "candidate_date": state.candidate.candidate_date.isoformat(),
                "orh_d": state.candidate.orh_d,
                "orl_d": state.candidate.orl_d,
            },
        )
        logger.info(
            "[ORHV] Signal: %s %s @ %.2f SL=%.2f win_rate=%.1f%%",
            signal_type, symbol, entry_price, stop_loss,
            state.candidate.win_rate * 100,
        )
        return event

    # ── Private helpers ───────────────────────────────────────────────────────

    def _in_entry_window(self, candle: CandleData) -> bool:
        t = candle.time
        minutes = t.hour * 60 + t.minute
        start = ORB_CLOSE_UTC_HOUR * 60 + ORB_CLOSE_UTC_MINUTE   # 240
        end = MAX_ENTRY_UTC_HOUR * 60 + MAX_ENTRY_UTC_MINUTE      # 390
        return start <= minutes <= end
