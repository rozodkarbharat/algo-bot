"""
ORHV Phase 2 — Historical Validation.

Pure Python — NO database calls, NO I/O.
Receives a list of prior ORHV setup dates for a symbol and the full candle
history, then simulates Phase 3 execution on the day AFTER each prior setup
to compute the historical win rate.

Anti-look-ahead guarantee:
  Only setups whose setup_date is STRICTLY BEFORE the candidate_date being
  validated are considered.  The caller is responsible for enforcing this;
  the validator asserts it defensively.

Qualification logic (from the strategy spec):
  wins >= qualification_min_wins   (typically 21 out of 30)
  OR
  win_rate >= qualification_min_win_rate  (typically 70%)
  AND occurrences_used >= min_occurrences_required (typically 5)

  If fewer occurrences are available than min_occurrences_required → not tradable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.models.backtest_trade import ExitReason, TradeSide
from app.models.historical_candle import CandleData
from app.strategy.strategies.opening_range_historical_validation.config import ORHVConfig
from app.strategy.strategies.opening_range_historical_validation.constants import (
    DEFAULT_LOOKBACK_OCCURRENCES,
    EOD_EXIT_UTC_HOUR,
    EOD_EXIT_UTC_MINUTE,
    MAX_ENTRY_UTC_HOUR,
    MAX_ENTRY_UTC_MINUTE,
    MIN_OCCURRENCES_REQUIRED,
    ORB_CLOSE_UTC_HOUR,
    ORB_CLOSE_UTC_MINUTE,
    QUALIFICATION_MIN_WIN_RATE,
    QUALIFICATION_MIN_WINS,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ORHVTradeOutcome:
    """Result of simulating one historical occurrence's next-day trade."""

    setup_date_str: str          # "YYYY-MM-DD" — Day D
    execution_date_str: str      # "YYYY-MM-DD" — Day D+1
    orh: float
    orl: float
    or_close: float
    orb_range_pct: float

    entry_price: Optional[float]
    stop_loss: Optional[float]
    exit_price: Optional[float]
    trade_side: Optional[str]    # "LONG" | "SHORT" | None
    exit_reason: Optional[str]   # "SL_HIT" | "EOD_EXIT" | "NO_BREAKOUT" | "RANGE_FILTER" | "TIME_FILTER" | "NO_DATA"

    pnl: float
    is_win: bool


@dataclass
class ORHVValidationOutcome:
    """Result returned by ORHVHistoricalValidator.validate()."""

    symbol: str
    occurrences_available: int    # total prior setups found
    occurrences_used: int         # capped at lookback_occurrences
    wins: int
    losses: int
    win_rate: float
    avg_pnl: float
    total_pnl: float
    tradable: bool
    rejection_reason: Optional[str]
    trade_outcomes: list[ORHVTradeOutcome] = field(default_factory=list)


class ORHVHistoricalValidator:
    """
    Validates a candidate ORHV setup against historical occurrences.

    Usage:
        validator = ORHVHistoricalValidator(config)
        outcome = validator.validate(
            symbol="RELIANCE",
            candidate_date=date(2024, 3, 15),
            prior_setup_dates=["2024-01-10", "2024-02-05", ...],
            candle_history={"RELIANCE": {"2024-01-10": [...], "2024-01-11": [...], ...}},
        )
        if outcome.tradable:
            # schedule for Day D+1 execution
    """

    def __init__(self, config: Optional[ORHVConfig] = None) -> None:
        self._cfg = config or ORHVConfig()

    # ── Public API ────────────────────────────────────────────────────────────

    def validate(
        self,
        symbol: str,
        candidate_date: date,
        prior_setup_dates: list[str],   # ISO "YYYY-MM-DD" strings, strictly before candidate_date
        candle_history: dict[str, list[CandleData]],  # date_str → sorted candle list
    ) -> ORHVValidationOutcome:
        """
        Simulate the last N historical occurrences and compute win statistics.

        Args:
            symbol:              NSE ticker.
            candidate_date:      Day D — the day being validated (strictly excluded
                                 from prior_setup_dates; caller must guarantee this).
            prior_setup_dates:   ISO date strings of all prior ORHV candidate days
                                 for this symbol, sorted oldest-first.
            candle_history:      date_str → sorted list of 15-min CandleData for Day D+1
                                 (the execution day for each setup).

        Returns:
            ORHVValidationOutcome with all fields populated.
        """
        candidate_str = candidate_date.isoformat()

        # Defensive: filter out any dates >= candidate_date (look-ahead guard)
        safe_dates = [d for d in prior_setup_dates if d < candidate_str]
        occurrences_available = len(safe_dates)

        # Take the most recent N occurrences
        lookback = self._cfg.lookback_occurrences
        selected = safe_dates[-lookback:]
        occurrences_used = len(selected)

        if occurrences_used < self._cfg.min_occurrences_required:
            return ORHVValidationOutcome(
                symbol=symbol,
                occurrences_available=occurrences_available,
                occurrences_used=occurrences_used,
                wins=0,
                losses=0,
                win_rate=0.0,
                avg_pnl=0.0,
                total_pnl=0.0,
                tradable=False,
                rejection_reason=(
                    f"Only {occurrences_used} prior occurrence(s); "
                    f"need >= {self._cfg.min_occurrences_required} for reliable statistics."
                ),
            )

        # ── Simulate each occurrence's next-day trade ─────────────────────────
        trade_outcomes: list[ORHVTradeOutcome] = []
        wins = 0
        total_pnl = 0.0

        for setup_date_str in selected:
            next_date_str = self._next_trading_date(setup_date_str, candle_history)
            if next_date_str is None:
                # No data for D+1 — skip this occurrence silently
                continue

            execution_candles = candle_history.get(next_date_str, [])
            outcome = self._simulate_phase3(
                setup_date_str=setup_date_str,
                execution_date_str=next_date_str,
                candles=execution_candles,
            )
            trade_outcomes.append(outcome)
            if outcome.is_win:
                wins += 1
            total_pnl += outcome.pnl

        # Update occurrences_used to reflect only those with D+1 data
        occurrences_used = len(trade_outcomes)

        if occurrences_used < self._cfg.min_occurrences_required:
            return ORHVValidationOutcome(
                symbol=symbol,
                occurrences_available=occurrences_available,
                occurrences_used=occurrences_used,
                wins=wins,
                losses=occurrences_used - wins,
                win_rate=0.0,
                avg_pnl=0.0,
                total_pnl=0.0,
                tradable=False,
                rejection_reason=(
                    f"Only {occurrences_used} occurrence(s) with D+1 data; "
                    f"need >= {self._cfg.min_occurrences_required}."
                ),
                trade_outcomes=trade_outcomes,
            )

        losses = occurrences_used - wins
        win_rate = wins / occurrences_used if occurrences_used > 0 else 0.0
        avg_pnl = total_pnl / occurrences_used if occurrences_used > 0 else 0.0

        # ── Qualification ─────────────────────────────────────────────────────
        # Either absolute wins OR win-rate must satisfy the threshold
        wins_ok = wins >= self._cfg.qualification_min_wins
        rate_ok = win_rate >= self._cfg.qualification_min_win_rate
        tradable = wins_ok or rate_ok

        rejection_reason: Optional[str] = None
        if not tradable:
            rejection_reason = (
                f"Wins {wins}/{occurrences_used} ({win_rate:.1%}) < "
                f"thresholds (wins>={self._cfg.qualification_min_wins} "
                f"OR rate>={self._cfg.qualification_min_win_rate:.0%})."
            )

        logger.debug(
            "[ORHV validate] %s: %d/%d wins (%.1f%%) tradable=%s",
            symbol, wins, occurrences_used, win_rate * 100, tradable,
        )

        return ORHVValidationOutcome(
            symbol=symbol,
            occurrences_available=occurrences_available,
            occurrences_used=occurrences_used,
            wins=wins,
            losses=losses,
            win_rate=round(win_rate, 6),
            avg_pnl=round(avg_pnl, 2),
            total_pnl=round(total_pnl, 2),
            tradable=tradable,
            rejection_reason=rejection_reason,
            trade_outcomes=trade_outcomes,
        )

    # ── Phase 3 simulation (used for historical occurrences) ──────────────────

    def _simulate_phase3(
        self,
        setup_date_str: str,
        execution_date_str: str,
        candles: list[CandleData],
    ) -> ORHVTradeOutcome:
        """
        Simulate one Phase 3 trade on the candles of Day D+1.

        Returns an ORHVTradeOutcome regardless of whether a trade was taken.
        """
        slippage_pct = self._cfg.slippage_pct
        brokerage = self._cfg.brokerage_per_side * 2  # entry + exit
        capital = self._cfg.capital_per_trade

        if not candles or len(candles) < 2:
            return ORHVTradeOutcome(
                setup_date_str=setup_date_str,
                execution_date_str=execution_date_str,
                orh=0.0, orl=0.0, or_close=0.0, orb_range_pct=0.0,
                entry_price=None, stop_loss=None, exit_price=None,
                trade_side=None, exit_reason="NO_DATA",
                pnl=0.0, is_win=False,
            )

        first = candles[0]
        orh = first.high
        orl = first.low
        or_close = first.close

        if or_close <= 0:
            return ORHVTradeOutcome(
                setup_date_str=setup_date_str,
                execution_date_str=execution_date_str,
                orh=orh, orl=orl, or_close=or_close, orb_range_pct=0.0,
                entry_price=None, stop_loss=None, exit_price=None,
                trade_side=None, exit_reason="NO_DATA",
                pnl=0.0, is_win=False,
            )

        orb_range_pct = (orh - orl) / or_close * 100.0

        # ── Range filter ──────────────────────────────────────────────────────
        if orb_range_pct > self._cfg.max_orb_range_pct:
            return ORHVTradeOutcome(
                setup_date_str=setup_date_str,
                execution_date_str=execution_date_str,
                orh=orh, orl=orl, or_close=or_close,
                orb_range_pct=round(orb_range_pct, 4),
                entry_price=None, stop_loss=None, exit_price=None,
                trade_side=None, exit_reason="RANGE_FILTER",
                pnl=0.0, is_win=False,
            )

        # ── Scan for first breakout within time window ────────────────────────
        entry_candle: Optional[CandleData] = None
        trade_side: Optional[str] = None

        for c in candles[1:]:
            if not self._in_entry_window(c):
                continue
            if c.close > orh:
                entry_candle = c
                trade_side = "LONG"
                break
            if c.close < orl:
                entry_candle = c
                trade_side = "SHORT"
                break

        if entry_candle is None:
            return ORHVTradeOutcome(
                setup_date_str=setup_date_str,
                execution_date_str=execution_date_str,
                orh=orh, orl=orl, or_close=or_close,
                orb_range_pct=round(orb_range_pct, 4),
                entry_price=None, stop_loss=None, exit_price=None,
                trade_side=None, exit_reason="NO_BREAKOUT",
                pnl=0.0, is_win=False,
            )

        # ── Entry fill with slippage ──────────────────────────────────────────
        if trade_side == "LONG":
            raw_entry = orh
            entry_price = round(raw_entry * (1.0 + slippage_pct / 100.0), 4)
            stop_loss = orl
        else:
            raw_entry = orl
            entry_price = round(raw_entry * (1.0 - slippage_pct / 100.0), 4)
            stop_loss = orh

        qty = max(1, math.floor(capital / entry_price))
        capital_used = qty * entry_price

        # ── Trade management candle-by-candle ─────────────────────────────────
        entry_idx = candles.index(entry_candle)
        post_entry = candles[entry_idx + 1:]

        exit_price: Optional[float] = None
        exit_reason_str = "EOD_EXIT"

        for c in post_entry:
            if trade_side == "LONG":
                if c.low <= stop_loss:
                    exit_price = round(stop_loss * (1.0 - slippage_pct / 100.0), 4)
                    exit_reason_str = "SL_HIT"
                    break
            else:
                if c.high >= stop_loss:
                    exit_price = round(stop_loss * (1.0 + slippage_pct / 100.0), 4)
                    exit_reason_str = "SL_HIT"
                    break

            if self._is_eod_candle(c):
                exit_price = c.close
                exit_reason_str = "EOD_EXIT"
                break

        if exit_price is None:
            last = candles[-1]
            exit_price = last.close
            exit_reason_str = "EOD_EXIT"

        # ── P&L ──────────────────────────────────────────────────────────────
        if trade_side == "LONG":
            gross_pnl = (exit_price - entry_price) * qty
        else:
            gross_pnl = (entry_price - exit_price) * qty

        net_pnl = gross_pnl - brokerage
        is_win = net_pnl > 0

        return ORHVTradeOutcome(
            setup_date_str=setup_date_str,
            execution_date_str=execution_date_str,
            orh=orh, orl=orl, or_close=or_close,
            orb_range_pct=round(orb_range_pct, 4),
            entry_price=entry_price,
            stop_loss=stop_loss,
            exit_price=exit_price,
            trade_side=trade_side,
            exit_reason=exit_reason_str,
            pnl=round(net_pnl, 2),
            is_win=is_win,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _in_entry_window(candle: CandleData) -> bool:
        """
        Return True if the candle's open time is within the Phase 3 entry window.

        Window: [9:30 IST (04:00 UTC), 12:00 IST (06:30 UTC)]
        Uses candle OPEN time to avoid look-ahead within the candle.
        """
        t = candle.time
        minutes = t.hour * 60 + t.minute
        start = ORB_CLOSE_UTC_HOUR * 60 + ORB_CLOSE_UTC_MINUTE    # 4:00 → 240
        end = MAX_ENTRY_UTC_HOUR * 60 + MAX_ENTRY_UTC_MINUTE       # 6:30 → 390
        return start <= minutes <= end

    @staticmethod
    def _is_eod_candle(candle: CandleData) -> bool:
        """Return True if this candle is at or after the 3:15 PM IST EOD exit."""
        t = candle.time
        minutes = t.hour * 60 + t.minute
        eod = EOD_EXIT_UTC_HOUR * 60 + EOD_EXIT_UTC_MINUTE        # 9:45 → 585
        return minutes >= eod

    @staticmethod
    def _next_trading_date(
        setup_date_str: str,
        candle_history: dict[str, list[CandleData]],
    ) -> Optional[str]:
        """
        Return the first date key in candle_history that is strictly after setup_date_str.

        This is Day D+1 in the backtest context.
        """
        later_dates = sorted(d for d in candle_history if d > setup_date_str)
        return later_dates[0] if later_dates else None


# ── Module-level default instance ────────────────────────────────────────────

default_validator = ORHVHistoricalValidator()
