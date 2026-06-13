"""
ORHV Backtest Engine — full three-phase historical replay.

Pure Python — NO database calls, NO I/O.
Implements the same run() interface as BacktestEngine so it is compatible
with BacktestService without any service-layer changes.

Flow per symbol per trading day D:
  1. Phase 1: Run ORHVSetupDetector on Day D candles.
  2. Accumulate detected setups into in-memory history (for Phase 2 lookback).
  3. If Day D produced a candidate:
       a. Phase 2: Validate against the last N prior setups using Phase 3 sim.
       b. If tradable: simulate Day D+1 trade with Phase 3 rules.
       c. Record SimulatedTrade result.

Anti-look-ahead guarantee:
  Phase 2 validation uses ONLY setups with setup_date < Day D.
  The accumulation step adds Day D's result AFTER the validation gate.

Interface compatibility:
  run(symbols, prob_scores, osd_history, candle_history) → BacktestEngineResult
  - prob_scores and osd_history are accepted but ignored (ORHV is self-contained).
  - candle_history must cover an extended range for Phase 2 to find 30 occurrences;
    BacktestService loads this via _load_candle_history() with an extended lookback.

Performance:
  CPU-bound pure Python — BacktestService runs this in a thread-pool executor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from app.models.backtest_trade import ExitReason, TradeSide
from app.models.historical_candle import CandleData
from app.strategy.backtest_engine import BacktestEngineResult
from app.strategy.strategies.opening_range_historical_validation.config import ORHVConfig
from app.strategy.strategies.opening_range_historical_validation.constants import (
    EOD_EXIT_UTC_HOUR,
    EOD_EXIT_UTC_MINUTE,
    MAX_ENTRY_UTC_HOUR,
    MAX_ENTRY_UTC_MINUTE,
    ORB_CLOSE_UTC_HOUR,
    ORB_CLOSE_UTC_MINUTE,
)
from app.strategy.strategies.opening_range_historical_validation.detector import (
    ORHVSetupDetector,
)
from app.strategy.strategies.opening_range_historical_validation.historical_validator import (
    ORHVHistoricalValidator,
)
from app.strategy.trade_simulator import SimulatedTrade
from app.utils.logger import get_logger
from app.utils.trading_day import get_trading_days

logger = get_logger(__name__)


class ORHVBacktestEngine:
    """
    Replays the Opening Range Historical Validation strategy over historical data.

    Usage:
        engine = ORHVBacktestEngine(config)
        result = engine.run(
            symbols=["RELIANCE", "TCS"],
            prob_scores={},          # not used — pass empty dict
            osd_history={},          # not used — pass empty dict
            candle_history={"RELIANCE": {"2024-01-10": [...], ...}},
        )

    candle_history must cover an extended window (5+ years recommended) so
    Phase 2 can find at least 30 prior occurrences before the backtest range.
    """

    def __init__(self, config: ORHVConfig) -> None:
        self._cfg = config
        self._detector = ORHVSetupDetector()
        self._validator = ORHVHistoricalValidator(config)

    # ── Public interface ──────────────────────────────────────────────────────

    def run(
        self,
        symbols: list[str],
        prob_scores: dict,         # ignored — accepted for interface compatibility
        osd_history: dict,         # ignored — accepted for interface compatibility
        candle_history: dict[str, dict[str, list[CandleData]]],
    ) -> BacktestEngineResult:
        """
        Execute the full ORHV backtest replay.

        Args:
            symbols:       Symbols to backtest.
            prob_scores:   Ignored (ORHV doesn't use OSD probability scores).
            osd_history:   Ignored (ORHV builds its own setup history from candles).
            candle_history: symbol → date_str → sorted list[CandleData].
                            Must cover both the validation lookback AND the
                            backtest execution range.

        Returns:
            BacktestEngineResult compatible with MetricsEngine.calculate().
        """
        result = BacktestEngineResult(symbols_processed=list(symbols))

        # Determine all available trading dates from candle_history
        all_dates: set[str] = set()
        for sym_data in candle_history.values():
            all_dates.update(sym_data.keys())
        sorted_dates = sorted(all_dates)

        result.trading_days_processed = len(sorted_dates)

        logger.info(
            "ORHVBacktestEngine starting: %d symbols × %d trading days (extended range)",
            len(symbols), len(sorted_dates),
        )

        # Per-symbol accumulated setup history (date_str list — grow as we process)
        # This enforces anti-look-ahead: when processing Day D, only D-1 and earlier
        # setups are in setup_history[symbol].
        setup_history: dict[str, list[str]] = {sym: [] for sym in symbols}

        for date_str in sorted_dates:
            for symbol in symbols:
                day_candles = candle_history.get(symbol, {}).get(date_str)
                if not day_candles:
                    continue

                # ── Phase 1: detect ORHV setup on this day ────────────────────
                detection = self._detector.detect(day_candles)

                # ── Phase 2 + 3: only if candidate AND sufficient history ──────
                if detection.is_candidate:
                    prior_dates = list(setup_history[symbol])  # copy before adding today
                    sym_candles = candle_history.get(symbol, {})

                    validation = self._validator.validate(
                        symbol=symbol,
                        candidate_date=date.fromisoformat(date_str),
                        prior_setup_dates=prior_dates,
                        candle_history=sym_candles,
                    )

                    if validation.tradable:
                        result.total_candidate_days += 1

                        # Find next trading date for Phase 3 simulation
                        next_date = self._next_date(date_str, sorted_dates)
                        if next_date is None:
                            result.total_no_data_days += 1
                        else:
                            next_candles = candle_history.get(symbol, {}).get(next_date)
                            if not next_candles:
                                result.total_no_data_days += 1
                            else:
                                trade = self._simulate_phase3_trade(
                                    symbol=symbol,
                                    setup_date_str=date_str,
                                    execution_date_str=next_date,
                                    candles=next_candles,
                                    win_rate=validation.win_rate,
                                    occurrences_used=validation.occurrences_used,
                                )
                                result.trades.append(trade)

                    # AFTER validation: add today to history so future days see it
                    setup_history[symbol].append(date_str)

        logger.info(
            "ORHVBacktestEngine complete: %d candidate days → %d trades",
            result.total_candidate_days, len(result.trades),
        )
        return result

    # ── Phase 3 simulation ────────────────────────────────────────────────────

    def _simulate_phase3_trade(
        self,
        symbol: str,
        setup_date_str: str,
        execution_date_str: str,
        candles: list[CandleData],
        win_rate: float,
        occurrences_used: int,
    ) -> SimulatedTrade:
        """
        Simulate the Phase 3 trade on Day D+1.

        Returns a SimulatedTrade compatible with MetricsEngine.
        """
        slippage_pct = self._cfg.slippage_pct
        brokerage = self._cfg.brokerage_per_side * 2
        capital = self._cfg.capital_per_trade

        try:
            execution_date = datetime.fromisoformat(execution_date_str).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            execution_date = datetime.now(timezone.utc)

        # Insufficient candles
        if not candles or len(candles) < 2:
            return self._no_breakout(
                symbol=symbol,
                execution_date=execution_date,
                win_rate=win_rate,
                reason="insufficient_candles",
            )

        first = candles[0]
        orh = first.high
        orl = first.low
        or_close = first.close

        if or_close <= 0:
            return self._no_breakout(
                symbol=symbol, execution_date=execution_date,
                win_rate=win_rate, reason="invalid_or_close",
            )

        orb_range_pct = (orh - orl) / or_close * 100.0

        # ── Range filter ──────────────────────────────────────────────────────
        if orb_range_pct > self._cfg.max_orb_range_pct:
            logger.debug(
                "[ORHV] %s %s: ORB range %.2f%% > %.2f%% — skipped.",
                symbol, execution_date_str, orb_range_pct, self._cfg.max_orb_range_pct,
            )
            return self._no_breakout(
                symbol=symbol, execution_date=execution_date,
                win_rate=win_rate, orh=orh, orl=orl,
                reason="range_filter",
            )

        # ── Scan for first breakout within entry window ───────────────────────
        entry_candle: Optional[CandleData] = None
        trade_side_val: Optional[TradeSide] = None
        breakout_side_str = "UP"

        for c in candles[1:]:
            if not self._in_entry_window(c):
                continue
            # Touch-based breakout: a candle that trades through ORH/ORL triggers
            # entry (no close confirmation). LONG takes priority when a single
            # candle straddles both levels.
            if c.high > orh:
                entry_candle = c
                trade_side_val = TradeSide.LONG
                breakout_side_str = "UP"
                break
            if c.low < orl:
                entry_candle = c
                trade_side_val = TradeSide.SHORT
                breakout_side_str = "DOWN"
                break

        if entry_candle is None or trade_side_val is None:
            return self._no_breakout(
                symbol=symbol, execution_date=execution_date,
                win_rate=win_rate, orh=orh, orl=orl, reason="no_breakout",
            )

        # ── Entry price with slippage ─────────────────────────────────────────
        if trade_side_val == TradeSide.LONG:
            raw_entry = orh
            entry_price = round(raw_entry * (1.0 + slippage_pct / 100.0), 4)
            stop_loss = orl
        else:
            raw_entry = orl
            entry_price = round(raw_entry * (1.0 - slippage_pct / 100.0), 4)
            stop_loss = orh

        qty = max(1, math.floor(capital / entry_price))
        capital_used = qty * entry_price

        # ── Simulate trade management ─────────────────────────────────────────
        entry_idx = candles.index(entry_candle)
        post_entry = candles[entry_idx + 1:]

        exit_time: Optional[datetime] = None
        exit_price: Optional[float] = None
        exit_reason = ExitReason.EOD_EXIT

        for c in post_entry:
            if trade_side_val == TradeSide.LONG:
                if c.low <= stop_loss:
                    exit_price = round(stop_loss * (1.0 - slippage_pct / 100.0), 4)
                    exit_time = c.time
                    exit_reason = ExitReason.SL_HIT
                    break
            else:
                if c.high >= stop_loss:
                    exit_price = round(stop_loss * (1.0 + slippage_pct / 100.0), 4)
                    exit_time = c.time
                    exit_reason = ExitReason.SL_HIT
                    break
            if self._is_eod_candle(c):
                exit_price = c.close
                exit_time = c.time
                exit_reason = ExitReason.EOD_EXIT
                break

        if exit_price is None:
            last = candles[-1]
            exit_price = last.close
            exit_time = last.time
            exit_reason = ExitReason.EOD_EXIT

        # ── P&L ──────────────────────────────────────────────────────────────
        if trade_side_val == TradeSide.LONG:
            gross_pnl = (exit_price - entry_price) * qty
        else:
            gross_pnl = (entry_price - exit_price) * qty

        net_pnl = gross_pnl - brokerage
        pnl_pct = (net_pnl / capital_used) * 100.0 if capital_used > 0 else 0.0

        # Risk-reward
        initial_risk = abs(entry_price - stop_loss)
        if initial_risk > 0 and exit_price is not None:
            achieved = (exit_price - entry_price if trade_side_val == TradeSide.LONG
                        else entry_price - exit_price)
            risk_reward: Optional[float] = round(achieved / initial_risk, 4)
        else:
            risk_reward = None

        return SimulatedTrade(
            symbol=symbol,
            trade_side=trade_side_val,
            breakout_side=breakout_side_str,
            orb_high=orh,
            orb_low=orl,
            probability_score=round(win_rate, 4),
            entry_time=entry_candle.time,
            entry_price=entry_price,
            stop_loss=stop_loss,
            exit_time=exit_time,
            exit_price=exit_price,
            exit_reason=exit_reason,
            quantity=qty,
            capital_used=capital_used,
            pnl=round(net_pnl, 2),
            pnl_percent=round(pnl_pct, 4),
            risk_reward=risk_reward,
            metadata={
                "setup_date": setup_date_str,
                "execution_date": execution_date_str,
                "orb_range_pct": round(orb_range_pct, 4),
                "occurrences_used": occurrences_used,
                "win_rate": round(win_rate, 4),
                "brokerage": brokerage,
                "gross_pnl": round(gross_pnl, 2),
                "strategy": "opening_range_historical_validation",
            },
        )

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _in_entry_window(candle: CandleData) -> bool:
        t = candle.time
        minutes = t.hour * 60 + t.minute
        start = ORB_CLOSE_UTC_HOUR * 60 + ORB_CLOSE_UTC_MINUTE   # 4:00 → 240
        end = MAX_ENTRY_UTC_HOUR * 60 + MAX_ENTRY_UTC_MINUTE      # 6:30 → 390
        return start <= minutes <= end

    @staticmethod
    def _is_eod_candle(candle: CandleData) -> bool:
        t = candle.time
        minutes = t.hour * 60 + t.minute
        eod = EOD_EXIT_UTC_HOUR * 60 + EOD_EXIT_UTC_MINUTE        # 9:45 → 585
        return minutes >= eod

    @staticmethod
    def _next_date(current: str, sorted_dates: list[str]) -> Optional[str]:
        """Return the first date in sorted_dates that is strictly after current."""
        for d in sorted_dates:
            if d > current:
                return d
        return None

    @staticmethod
    def _no_breakout(
        symbol: str,
        execution_date: datetime,
        win_rate: float,
        orh: float = 0.0,
        orl: float = 0.0,
        reason: str = "no_breakout",
    ) -> SimulatedTrade:
        sl = orl if orl > 0 else 0.0
        return SimulatedTrade(
            symbol=symbol,
            trade_side=TradeSide.LONG,       # placeholder — no trade taken
            breakout_side="UP",
            orb_high=orh,
            orb_low=orl,
            probability_score=round(win_rate, 4),
            entry_time=None,
            entry_price=None,
            stop_loss=sl,
            exit_time=None,
            exit_price=None,
            exit_reason=ExitReason.NO_BREAKOUT,
            quantity=0,
            capital_used=0.0,
            pnl=0.0,
            pnl_percent=0.0,
            risk_reward=None,
            metadata={
                "rejection_reason": reason,
                "execution_date": execution_date.date().isoformat() if execution_date else "",
                "strategy": "opening_range_historical_validation",
            },
        )
