"""
Trade simulation engine for the One-Side ORB strategy.

Pure Python — NO database calls, NO broker imports, NO I/O.
Receives candle data and a trade setup; returns a SimulatedTrade result.

Simulation assumptions:
  - Entry: triggered when a 15-min candle CLOSES above ORB high (LONG) or
            below ORB low (SHORT). Close-based confirmation avoids intra-bar
            look-ahead bias.
  - Entry price: orb_high (LONG) or orb_low (SHORT) + slippage. This models
    a stop-buy order at the ORB boundary rather than chasing the close price.
  - Stop loss check: per-candle worst-case — if candle.low ≤ stop (LONG)
    the SL is hit at the stop price. Exit price includes adverse slippage.
  - EOD exit: last candle at or after eod_exit_utc_hour:eod_exit_utc_minute
    is used as the exit candle; exit = candle.close.
  - Brokerage: flat per-side cost charged for both entry and exit.
  - Slippage: applied as a % of the trigger / exit price.
  - Quantity: floor(capital_per_trade / entry_price) — whole shares only.
  - One trade per symbol per day (enforced by the calling BacktestEngine).

Scalability:
  - Stateless: safe to parallelise with asyncio / thread-pool.
  - No global state — all parameters passed explicitly per call.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.models.backtest_trade import ExitReason, TradeSide
from app.models.historical_candle import CandleData
from app.utils.logger import get_logger

logger = get_logger(__name__)

# IST = UTC+05:30.  Entry window start: 9:30 IST = 04:00 UTC.
# These are hour/minute values of candle OPEN timestamps in UTC.
_ENTRY_WINDOW_START_UTC_HOUR = 4
_ENTRY_WINDOW_START_UTC_MINUTE = 0

# EOD forced exit: 3:15 PM IST = 09:45 UTC
_EOD_EXIT_UTC_HOUR = 9
_EOD_EXIT_UTC_MINUTE = 45


@dataclass(frozen=True)
class TradeSetup:
    """
    Input to TradeSimulator.simulate() — fully describes one candidate day.

    Created by BacktestEngine for each (symbol, date) where:
      - Yesterday was OSD with continuation_probability >= threshold
      - Today's first candle range <= max_orb_range_pct
    """

    symbol: str
    trade_side: TradeSide          # LONG (UP continuation) or SHORT (DOWN continuation)
    breakout_side: str             # "UP" or "DOWN" — yesterday's OSD direction
    orb_high: float                # Today's first candle high
    orb_low: float                 # Today's first candle low
    probability_score: float       # Continuation probability at decision time

    # Entry window (UTC) — candle open time must be within this window
    entry_window_end_utc_hour: int = 6    # 11:30 IST = 06:00 UTC default
    entry_window_end_utc_minute: int = 0

    # Risk / cost parameters
    sl_buffer_pct: float = 0.0        # extra % buffer added to SL beyond ORB boundary
    slippage_pct: float = 0.05        # % slippage on entry and exit
    brokerage_per_side: float = 20.0  # ₹ flat brokerage per trade side
    capital_per_trade: float = 100_000.0


@dataclass
class SimulatedTrade:
    """
    Result from TradeSimulator.simulate() — the raw trade outcome.

    Converted to BacktestTrade (Beanie document) by BacktestService for DB storage.
    """

    symbol: str
    trade_side: TradeSide
    breakout_side: str
    orb_high: float
    orb_low: float
    probability_score: float

    entry_time: Optional[datetime]
    entry_price: Optional[float]
    stop_loss: float

    exit_time: Optional[datetime]
    exit_price: Optional[float]
    exit_reason: ExitReason

    quantity: int
    capital_used: float
    pnl: float           # net P&L after brokerage (0.0 for NO_BREAKOUT)
    pnl_percent: float   # pnl / capital_used × 100 (0.0 for NO_BREAKOUT)
    risk_reward: Optional[float]

    metadata: dict = field(default_factory=dict)


class TradeSimulator:
    """
    Simulates a single One-Side ORB trade on a given set of 15-min candles.

    Usage:
        simulator = TradeSimulator()
        result = simulator.simulate(setup, candles)
    """

    def simulate(
        self,
        setup: TradeSetup,
        candles: list[CandleData],
    ) -> SimulatedTrade:
        """
        Simulate the full trade lifecycle for one (symbol, date) setup.

        Args:
            setup:   TradeSetup describing the candidate.
            candles: All 15-min candles for the trade day, sorted chronologically.
                     First element = ORB candle (9:15–9:30 IST).

        Returns:
            SimulatedTrade with all fields populated.
        """
        if not candles or len(candles) < 2:
            return self._no_breakout(setup, reason="insufficient_candles")

        orb_high = setup.orb_high
        orb_low = setup.orb_low

        # Compute stop loss with buffer
        if setup.trade_side == TradeSide.LONG:
            stop_loss = orb_low * (1.0 - setup.sl_buffer_pct / 100.0)
        else:
            stop_loss = orb_high * (1.0 + setup.sl_buffer_pct / 100.0)

        # Scan candles AFTER the first candle (index 0) for entry opportunity
        entry_candle: Optional[CandleData] = None

        for candle in candles[1:]:
            if not self._in_entry_window(candle, setup):
                continue

            if setup.trade_side == TradeSide.LONG and candle.close > orb_high:
                entry_candle = candle
                break
            elif setup.trade_side == TradeSide.SHORT and candle.close < orb_low:
                entry_candle = candle
                break

        if entry_candle is None:
            return self._no_breakout(setup, reason="no_breakout_in_window", stop_loss=stop_loss)

        # ── Entry ──────────────────────────────────────────────────────────────
        if setup.trade_side == TradeSide.LONG:
            # Enter at ORB high (stop-buy price) + slippage — models realistic fill
            raw_entry = orb_high
        else:
            raw_entry = orb_low

        entry_price = self._apply_slippage_entry(raw_entry, setup.trade_side, setup.slippage_pct)
        quantity = max(1, math.floor(setup.capital_per_trade / entry_price))
        capital_used = quantity * entry_price

        # ── Simulate trade progression candle-by-candle ───────────────────────
        # Start checking from the candle AFTER entry (entry filled at end of entry_candle)
        entry_idx = candles.index(entry_candle)
        post_entry_candles = candles[entry_idx + 1 :]

        exit_time: Optional[datetime] = None
        exit_price: Optional[float] = None
        exit_reason = ExitReason.EOD_EXIT  # default unless SL is hit earlier

        for candle in post_entry_candles:
            # Check SL first (worst-case intra-bar)
            if setup.trade_side == TradeSide.LONG:
                if candle.low <= stop_loss:
                    exit_price = self._apply_slippage_exit(
                        stop_loss, setup.trade_side, setup.slippage_pct
                    )
                    exit_time = candle.time
                    exit_reason = ExitReason.SL_HIT
                    break
            else:
                if candle.high >= stop_loss:
                    exit_price = self._apply_slippage_exit(
                        stop_loss, setup.trade_side, setup.slippage_pct
                    )
                    exit_time = candle.time
                    exit_reason = ExitReason.SL_HIT
                    break

            # Check EOD exit
            if self._is_eod_candle(candle):
                exit_price = candle.close
                exit_time = candle.time
                exit_reason = ExitReason.EOD_EXIT
                break

        # If loop exhausted without explicit exit, use last candle close (EOD)
        if exit_price is None and candles:
            last_candle = candles[-1]
            exit_price = last_candle.close
            exit_time = last_candle.time
            exit_reason = ExitReason.EOD_EXIT

        # If still no exit (edge case: no candles after entry), EOD at entry candle
        if exit_price is None:
            exit_price = entry_candle.close
            exit_time = entry_candle.time
            exit_reason = ExitReason.EOD_EXIT

        # ── P&L ───────────────────────────────────────────────────────────────
        brokerage = setup.brokerage_per_side * 2  # entry + exit

        if setup.trade_side == TradeSide.LONG:
            gross_pnl = (exit_price - entry_price) * quantity
        else:
            gross_pnl = (entry_price - exit_price) * quantity

        net_pnl = gross_pnl - brokerage
        pnl_percent = (net_pnl / capital_used) * 100.0 if capital_used > 0 else 0.0

        # Risk-reward: achieved gain / initial risk
        initial_risk_per_share = abs(entry_price - stop_loss)
        if initial_risk_per_share > 0 and exit_price is not None:
            if setup.trade_side == TradeSide.LONG:
                achieved = exit_price - entry_price
            else:
                achieved = entry_price - exit_price
            risk_reward = achieved / initial_risk_per_share
        else:
            risk_reward = None

        return SimulatedTrade(
            symbol=setup.symbol,
            trade_side=setup.trade_side,
            breakout_side=setup.breakout_side,
            orb_high=orb_high,
            orb_low=orb_low,
            probability_score=setup.probability_score,
            entry_time=entry_candle.time,
            entry_price=entry_price,
            stop_loss=stop_loss,
            exit_time=exit_time,
            exit_price=exit_price,
            exit_reason=exit_reason,
            quantity=quantity,
            capital_used=capital_used,
            pnl=round(net_pnl, 2),
            pnl_percent=round(pnl_percent, 4),
            risk_reward=round(risk_reward, 4) if risk_reward is not None else None,
            metadata={
                "entry_candle_close": entry_candle.close,
                "orb_range_pct": round(
                    (orb_high - orb_low) / orb_low * 100, 4
                ) if orb_low > 0 else 0.0,
                "brokerage": brokerage,
                "gross_pnl": round(gross_pnl, 2),
            },
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _in_entry_window(candle: CandleData, setup: TradeSetup) -> bool:
        """
        Return True if the candle's open time falls in the valid entry window.

        Entry window (UTC): [4:00, setup.entry_window_end_utc_hour:minute]
        These correspond to 9:30 AM IST → max_entry_time IST.
        """
        t = candle.time
        candle_minutes = t.hour * 60 + t.minute
        start_minutes = _ENTRY_WINDOW_START_UTC_HOUR * 60 + _ENTRY_WINDOW_START_UTC_MINUTE
        end_minutes = setup.entry_window_end_utc_hour * 60 + setup.entry_window_end_utc_minute
        return start_minutes <= candle_minutes <= end_minutes

    @staticmethod
    def _is_eod_candle(candle: CandleData) -> bool:
        """Return True if this candle is at or after the EOD exit time (3:15 IST = 9:45 UTC)."""
        t = candle.time
        candle_minutes = t.hour * 60 + t.minute
        eod_minutes = _EOD_EXIT_UTC_HOUR * 60 + _EOD_EXIT_UTC_MINUTE
        return candle_minutes >= eod_minutes

    @staticmethod
    def _apply_slippage_entry(price: float, side: TradeSide, slippage_pct: float) -> float:
        """Add slippage to entry — unfavourable: higher for LONG, lower for SHORT."""
        factor = 1.0 + slippage_pct / 100.0 if side == TradeSide.LONG else 1.0 - slippage_pct / 100.0
        return round(price * factor, 4)

    @staticmethod
    def _apply_slippage_exit(price: float, side: TradeSide, slippage_pct: float) -> float:
        """Add slippage to exit — unfavourable: lower for LONG (SL), higher for SHORT (SL)."""
        factor = 1.0 - slippage_pct / 100.0 if side == TradeSide.LONG else 1.0 + slippage_pct / 100.0
        return round(price * factor, 4)

    @staticmethod
    def _no_breakout(
        setup: TradeSetup,
        reason: str = "no_breakout",
        stop_loss: Optional[float] = None,
    ) -> SimulatedTrade:
        """Return a zero-P&L result for a day where no entry was taken."""
        if stop_loss is None:
            if setup.trade_side == TradeSide.LONG:
                stop_loss = setup.orb_low * (1.0 - setup.sl_buffer_pct / 100.0)
            else:
                stop_loss = setup.orb_high * (1.0 + setup.sl_buffer_pct / 100.0)

        return SimulatedTrade(
            symbol=setup.symbol,
            trade_side=setup.trade_side,
            breakout_side=setup.breakout_side,
            orb_high=setup.orb_high,
            orb_low=setup.orb_low,
            probability_score=setup.probability_score,
            entry_time=None,
            entry_price=None,
            stop_loss=stop_loss,
            exit_time=None,
            exit_price=None,
            exit_reason=ExitReason.NO_BREAKOUT,
            quantity=0,
            capital_used=0.0,
            pnl=0.0,
            pnl_percent=0.0,
            risk_reward=None,
            metadata={"rejection_reason": reason},
        )


# Module-level default instance — stateless, reusable
default_simulator = TradeSimulator()
