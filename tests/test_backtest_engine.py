"""
Unit tests for the BacktestEngine.

Tests cover:
  - Skipping symbols with no OSD yesterday
  - Skipping symbols below probability threshold
  - Skipping days with missing candles
  - Skipping days with first-candle range > max_orb_range_pct
  - Correct direction (LONG for UP, SHORT for DOWN)
  - Full replay across multiple days and symbols

All tests are pure Python — no database, no broker, no I/O.

Run with:
    pytest tests/test_backtest_engine.py -v
"""

from datetime import date, datetime, timezone

import pytest

from app.models.backtest_trade import ExitReason, TradeSide
from app.models.historical_candle import CandleData
from app.strategy.backtest_engine import BacktestConfig, BacktestEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _candle(utc_h: int, utc_m: int, o: float, h: float, lo: float, c: float) -> CandleData:
    return CandleData(
        time=datetime(2024, 6, 3, utc_h, utc_m, 0, tzinfo=timezone.utc),
        open=o, high=h, low=lo, close=c, volume=300_000,
    )


def _day_candles(day: date, orb_h: float, orb_l: float, breakout: bool, direction: str) -> list[CandleData]:
    """
    Build a minimal candle list for a trading day.

    If breakout=True, adds a breakout candle in `direction` immediately after ORB.
    Always adds an EOD candle.
    """
    orb = CandleData(
        time=datetime(day.year, day.month, day.day, 3, 45, tzinfo=timezone.utc),
        open=(orb_h + orb_l) / 2, high=orb_h, low=orb_l,
        close=(orb_h + orb_l) / 2, volume=500_000,
    )
    eod = CandleData(
        time=datetime(day.year, day.month, day.day, 9, 45, tzinfo=timezone.utc),
        open=orb_h, high=orb_h + 10, low=orb_l - 10, close=orb_h + 5,
        volume=200_000,
    )
    if not breakout:
        return [orb, eod]

    if direction == "UP":
        bo = CandleData(
            time=datetime(day.year, day.month, day.day, 4, 0, tzinfo=timezone.utc),
            open=orb_h, high=orb_h + 15, low=orb_h - 1, close=orb_h + 10,
            volume=600_000,
        )
    else:
        bo = CandleData(
            time=datetime(day.year, day.month, day.day, 4, 0, tzinfo=timezone.utc),
            open=orb_l, high=orb_l + 1, low=orb_l - 15, close=orb_l - 10,
            volume=600_000,
        )
    return [orb, bo, eod]


def _config(
    from_date: date = date(2024, 6, 3),
    to_date: date = date(2024, 6, 7),
    prob_threshold: float = 0.70,
    max_orb_range_pct: float = 1.0,
    max_entry_time_ist: str = "11:30",
    capital: float = 100_000.0,
) -> BacktestConfig:
    return BacktestConfig(
        from_date=from_date,
        to_date=to_date,
        probability_threshold=prob_threshold,
        max_orb_range_pct=max_orb_range_pct,
        max_entry_time_ist=max_entry_time_ist,
        capital_per_trade=capital,
        slippage_pct=0.0,
        brokerage_per_side=0.0,
        sl_buffer_pct=0.0,
    )


# ── Gating tests ──────────────────────────────────────────────────────────────

class TestGates:
    def test_no_osd_yesterday_skips_symbol(self) -> None:
        """Symbol with no OSD yesterday produces no trades."""
        day = date(2024, 6, 3)
        engine = BacktestEngine(_config(from_date=day, to_date=day))

        osd_history = {"RELIANCE": {}}   # no records
        prob_scores  = {"RELIANCE": 0.80}
        candle_history = {
            "RELIANCE": {
                day.isoformat(): _day_candles(day, 1000, 990, breakout=True, direction="UP")
            }
        }

        result = engine.run(["RELIANCE"], prob_scores, osd_history, candle_history)
        assert len(result.trades) == 0

    def test_low_probability_skips_symbol(self) -> None:
        """Symbol with probability below threshold is excluded."""
        day = date(2024, 6, 3)
        prev = date(2024, 6, 2)
        engine = BacktestEngine(_config(from_date=day, to_date=day, prob_threshold=0.70))

        osd_history = {"RELIANCE": {prev.isoformat(): {"is_one_side": True, "direction": "UP"}}}
        prob_scores  = {"RELIANCE": 0.60}   # below 0.70 threshold
        candle_history = {
            "RELIANCE": {day.isoformat(): _day_candles(day, 1000, 990, True, "UP")}
        }

        result = engine.run(["RELIANCE"], prob_scores, osd_history, candle_history)
        assert len(result.trades) == 0

    def test_missing_candles_skips_day(self) -> None:
        """Day with no candle data is skipped and counted in no_data_days."""
        day = date(2024, 6, 3)
        prev = date(2024, 6, 2)
        engine = BacktestEngine(_config(from_date=day, to_date=day))

        osd_history = {"RELIANCE": {prev.isoformat(): {"is_one_side": True, "direction": "UP"}}}
        prob_scores  = {"RELIANCE": 0.75}
        candle_history = {"RELIANCE": {}}   # no candles for this day

        result = engine.run(["RELIANCE"], prob_scores, osd_history, candle_history)
        assert len(result.trades) == 0
        assert result.total_no_data_days == 1

    def test_wide_orb_skips_day(self) -> None:
        """Day where ORB range > max_orb_range_pct is skipped."""
        day = date(2024, 6, 3)
        prev = date(2024, 6, 2)
        # ORB high=1020, low=1000 → range = 20/1000 = 2.0% > 1.0%
        engine = BacktestEngine(_config(from_date=day, to_date=day, max_orb_range_pct=1.0))

        osd_history = {"RELIANCE": {prev.isoformat(): {"is_one_side": True, "direction": "UP"}}}
        prob_scores  = {"RELIANCE": 0.75}
        candle_history = {
            "RELIANCE": {day.isoformat(): _day_candles(day, 1020, 1000, True, "UP")}
        }

        result = engine.run(["RELIANCE"], prob_scores, osd_history, candle_history)
        assert len(result.trades) == 0

    def test_choppy_osd_yesterday_skips(self) -> None:
        """Yesterday was OSD but direction=None (choppy) → skip."""
        day = date(2024, 6, 3)
        prev = date(2024, 6, 2)
        engine = BacktestEngine(_config(from_date=day, to_date=day))

        osd_history = {"RELIANCE": {prev.isoformat(): {"is_one_side": True, "direction": None}}}
        prob_scores  = {"RELIANCE": 0.80}
        candle_history = {
            "RELIANCE": {day.isoformat(): _day_candles(day, 1000, 995, True, "UP")}
        }

        result = engine.run(["RELIANCE"], prob_scores, osd_history, candle_history)
        assert len(result.trades) == 0


# ── Direction tests ───────────────────────────────────────────────────────────

class TestDirection:
    def test_up_direction_produces_long_trade(self) -> None:
        """Yesterday UP → today LONG setup."""
        day = date(2024, 6, 3)
        prev = date(2024, 6, 2)
        engine = BacktestEngine(_config(from_date=day, to_date=day))

        osd_history = {"RELIANCE": {prev.isoformat(): {"is_one_side": True, "direction": "UP"}}}
        prob_scores  = {"RELIANCE": 0.80}
        candle_history = {
            "RELIANCE": {day.isoformat(): _day_candles(day, 1000, 995, True, "UP")}
        }

        result = engine.run(["RELIANCE"], prob_scores, osd_history, candle_history)
        assert len(result.trades) == 1
        assert result.trades[0].trade_side == TradeSide.LONG

    def test_down_direction_produces_short_trade(self) -> None:
        """Yesterday DOWN → today SHORT setup."""
        day = date(2024, 6, 3)
        prev = date(2024, 6, 2)
        engine = BacktestEngine(_config(from_date=day, to_date=day))

        osd_history = {"RELIANCE": {prev.isoformat(): {"is_one_side": True, "direction": "DOWN"}}}
        prob_scores  = {"RELIANCE": 0.80}
        candle_history = {
            "RELIANCE": {day.isoformat(): _day_candles(day, 1000, 995, True, "DOWN")}
        }

        result = engine.run(["RELIANCE"], prob_scores, osd_history, candle_history)
        assert len(result.trades) == 1
        assert result.trades[0].trade_side == TradeSide.SHORT


# ── Multi-symbol / multi-day ──────────────────────────────────────────────────

class TestMultiDay:
    def test_multiple_symbols_on_same_day(self) -> None:
        """Two symbols both qualify → two trades."""
        day = date(2024, 6, 3)
        prev = date(2024, 6, 2)
        engine = BacktestEngine(_config(from_date=day, to_date=day))

        symbols = ["RELIANCE", "TCS"]
        osd_history = {
            sym: {prev.isoformat(): {"is_one_side": True, "direction": "UP"}}
            for sym in symbols
        }
        prob_scores = {sym: 0.75 for sym in symbols}
        candle_history = {
            sym: {day.isoformat(): _day_candles(day, 1000, 995, True, "UP")}
            for sym in symbols
        }

        result = engine.run(symbols, prob_scores, osd_history, candle_history)
        assert len(result.trades) == 2
        assert result.total_candidate_days == 2

    def test_candidate_days_counted_for_no_breakout(self) -> None:
        """Days with no breakout are still counted as candidate_days."""
        day = date(2024, 6, 3)
        prev = date(2024, 6, 2)
        engine = BacktestEngine(_config(from_date=day, to_date=day))

        osd_history = {"RELIANCE": {prev.isoformat(): {"is_one_side": True, "direction": "UP"}}}
        prob_scores  = {"RELIANCE": 0.80}
        # Candles that never break above ORB high
        candle_history = {
            "RELIANCE": {day.isoformat(): _day_candles(day, 1000, 995, breakout=False, direction="UP")}
        }

        result = engine.run(["RELIANCE"], prob_scores, osd_history, candle_history)
        assert result.total_candidate_days == 1
        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == ExitReason.NO_BREAKOUT
