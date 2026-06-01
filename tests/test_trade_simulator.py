"""
Unit tests for the TradeSimulator.

All tests are pure Python — no database, no broker, no I/O.
Each test constructs synthetic 15-min CandleData and asserts
the simulator's entry, SL, EOD, and P&L behaviour.

Run with:
    pytest tests/test_trade_simulator.py -v
"""

from datetime import datetime, timezone

import pytest

from app.models.backtest_trade import ExitReason, TradeSide
from app.models.historical_candle import CandleData
from app.strategy.trade_simulator import TradeSetup, TradeSimulator


# ── Helpers ───────────────────────────────────────────────────────────────────

def _candle(utc_hour: int, utc_min: int, open_: float, high: float, low: float, close: float) -> CandleData:
    """Build a synthetic 15-min CandleData with the given UTC open time."""
    return CandleData(
        time=datetime(2024, 6, 3, utc_hour, utc_min, 0, tzinfo=timezone.utc),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=500_000,
    )


def _orb_candle(high: float, low: float) -> CandleData:
    """Build the ORB first candle at 9:15 IST = 3:45 UTC."""
    mid = (high + low) / 2
    return _candle(3, 45, mid, high, low, mid)


def _setup(
    trade_side: TradeSide,
    orb_high: float,
    orb_low: float,
    prob: float = 0.75,
    capital: float = 100_000.0,
    slippage_pct: float = 0.0,
    brokerage_per_side: float = 0.0,
    entry_end_h: int = 6,
    entry_end_m: int = 0,
    sl_buffer_pct: float = 0.0,
) -> TradeSetup:
    return TradeSetup(
        symbol="TESTSTOCK",
        trade_side=trade_side,
        breakout_side="UP" if trade_side == TradeSide.LONG else "DOWN",
        orb_high=orb_high,
        orb_low=orb_low,
        probability_score=prob,
        entry_window_end_utc_hour=entry_end_h,
        entry_window_end_utc_minute=entry_end_m,
        sl_buffer_pct=sl_buffer_pct,
        slippage_pct=slippage_pct,
        brokerage_per_side=brokerage_per_side,
        capital_per_trade=capital,
    )


@pytest.fixture
def simulator() -> TradeSimulator:
    return TradeSimulator()


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_candles_returns_no_breakout(self, simulator: TradeSimulator) -> None:
        setup = _setup(TradeSide.LONG, 1000, 990)
        result = simulator.simulate(setup, [])
        assert result.exit_reason == ExitReason.NO_BREAKOUT
        assert result.pnl == 0.0
        assert result.entry_price is None

    def test_single_candle_returns_no_breakout(self, simulator: TradeSimulator) -> None:
        orb = _orb_candle(1000, 990)
        setup = _setup(TradeSide.LONG, 1000, 990)
        result = simulator.simulate(setup, [orb])
        assert result.exit_reason == ExitReason.NO_BREAKOUT


# ── Bullish breakout ──────────────────────────────────────────────────────────

class TestBullishBreakout:
    def test_long_entry_above_orb_high(self, simulator: TradeSimulator) -> None:
        """LONG entry when a candle closes above ORB high (no slippage)."""
        orb = _orb_candle(1000, 990)
        # 9:30 candle breaks above ORB high (1000)
        c1 = _candle(4, 0, 1000, 1010, 999, 1005)   # close > 1000 → triggers entry
        c2 = _candle(4, 15, 1005, 1015, 1004, 1012)
        # EOD candle
        eod = _candle(9, 45, 1012, 1015, 1011, 1013)

        setup = _setup(TradeSide.LONG, orb_high=1000, orb_low=990, slippage_pct=0.0)
        result = simulator.simulate(setup, [orb, c1, c2, eod])

        assert result.exit_reason == ExitReason.EOD_EXIT
        assert result.entry_price == pytest.approx(1000.0)   # triggered at orb_high
        assert result.exit_price == pytest.approx(1013.0)    # EOD close
        assert result.pnl > 0

    def test_long_quantity_calculation(self, simulator: TradeSimulator) -> None:
        """qty = floor(capital / entry_price)."""
        orb = _orb_candle(500, 490)
        c1  = _candle(4, 0, 500, 510, 499, 502)
        eod = _candle(9, 45, 502, 505, 501, 504)

        setup = _setup(TradeSide.LONG, 500, 490, capital=100_000.0, slippage_pct=0.0)
        result = simulator.simulate(setup, [orb, c1, eod])

        assert result.quantity == 200   # floor(100000 / 500) = 200
        assert result.capital_used == pytest.approx(100_000.0)

    def test_long_sl_hit(self, simulator: TradeSimulator) -> None:
        """SL triggered when a post-entry candle's low <= stop_loss."""
        orb = _orb_candle(1000, 990)
        c1  = _candle(4, 0, 1000, 1010, 999, 1005)   # entry triggered
        c2  = _candle(4, 15, 1005, 1007, 985, 988)    # low=985 ≤ SL=990

        setup = _setup(TradeSide.LONG, 1000, 990, slippage_pct=0.0)
        result = simulator.simulate(setup, [orb, c1, c2])

        assert result.exit_reason == ExitReason.SL_HIT
        assert result.exit_price == pytest.approx(990.0)  # exit at stop_loss
        assert result.pnl < 0

    def test_long_no_breakout(self, simulator: TradeSimulator) -> None:
        """No trade when price never closes above ORB high."""
        orb = _orb_candle(1000, 990)
        c1  = _candle(4, 0, 1000, 999, 991, 995)   # close=995 < 1000
        c2  = _candle(4, 15, 995, 998, 992, 996)   # close=996 < 1000
        eod = _candle(9, 45, 996, 999, 993, 997)

        setup = _setup(TradeSide.LONG, 1000, 990)
        result = simulator.simulate(setup, [orb, c1, c2, eod])

        assert result.exit_reason == ExitReason.NO_BREAKOUT
        assert result.pnl == 0.0

    def test_long_breakout_after_entry_window_is_ignored(self, simulator: TradeSimulator) -> None:
        """Entry candle after the max_entry_time should NOT trigger an entry."""
        orb = _orb_candle(1000, 990)
        # 07:00 UTC = 12:30 IST — after 11:30 IST cutoff
        late = _candle(7, 0, 1000, 1010, 999, 1006)
        eod  = _candle(9, 45, 1006, 1008, 1005, 1007)

        # entry window closes at 6:00 UTC (11:30 IST)
        setup = _setup(TradeSide.LONG, 1000, 990, entry_end_h=6, entry_end_m=0)
        result = simulator.simulate(setup, [orb, late, eod])

        assert result.exit_reason == ExitReason.NO_BREAKOUT


# ── Bearish breakout ──────────────────────────────────────────────────────────

class TestBearishBreakout:
    def test_short_entry_below_orb_low(self, simulator: TradeSimulator) -> None:
        """SHORT entry when a candle closes below ORB low."""
        orb = _orb_candle(1000, 990)
        c1  = _candle(4, 0, 990, 991, 980, 985)   # close=985 < 990 → SHORT entry
        eod = _candle(9, 45, 985, 986, 975, 978)

        setup = _setup(TradeSide.SHORT, 1000, 990, slippage_pct=0.0)
        result = simulator.simulate(setup, [orb, c1, eod])

        assert result.exit_reason == ExitReason.EOD_EXIT
        assert result.entry_price == pytest.approx(990.0)   # orb_low trigger
        assert result.exit_price == pytest.approx(978.0)
        assert result.pnl > 0   # SHORT profits on decline

    def test_short_sl_hit(self, simulator: TradeSimulator) -> None:
        """SHORT SL triggered when post-entry candle's high >= stop_loss (orb_high)."""
        orb = _orb_candle(1000, 990)
        c1  = _candle(4, 0, 990, 991, 980, 985)   # SHORT entry
        c2  = _candle(4, 15, 985, 1005, 984, 990)  # high=1005 >= SL=1000

        setup = _setup(TradeSide.SHORT, 1000, 990, slippage_pct=0.0)
        result = simulator.simulate(setup, [orb, c1, c2])

        assert result.exit_reason == ExitReason.SL_HIT
        assert result.exit_price == pytest.approx(1000.0)
        assert result.pnl < 0


# ── Slippage tests ────────────────────────────────────────────────────────────

class TestSlippage:
    def test_long_entry_includes_slippage(self, simulator: TradeSimulator) -> None:
        """Entry price = orb_high * (1 + slippage) for LONG."""
        orb = _orb_candle(1000, 990)
        c1  = _candle(4, 0, 1000, 1010, 999, 1005)
        eod = _candle(9, 45, 1005, 1010, 1004, 1008)

        setup = _setup(TradeSide.LONG, 1000, 990, slippage_pct=0.1, brokerage_per_side=0.0)
        result = simulator.simulate(setup, [orb, c1, eod])

        # Entry = 1000 * 1.001 = 1001.0
        assert result.entry_price == pytest.approx(1000 * 1.001, rel=1e-4)

    def test_short_sl_exit_includes_adverse_slippage(self, simulator: TradeSimulator) -> None:
        """On SL exit for SHORT, exit price = stop_loss * (1 + slippage)."""
        orb = _orb_candle(1000, 990)
        c1  = _candle(4, 0, 990, 991, 980, 985)
        c2  = _candle(4, 15, 985, 1005, 984, 990)  # SL hit

        setup = _setup(TradeSide.SHORT, 1000, 990, slippage_pct=0.1, brokerage_per_side=0.0)
        result = simulator.simulate(setup, [orb, c1, c2])

        # SL exit price = 1000 * 1.001 = 1001.0 (adverse for SHORT)
        assert result.exit_price == pytest.approx(1000 * 1.001, rel=1e-4)


# ── Brokerage tests ───────────────────────────────────────────────────────────

class TestBrokerage:
    def test_brokerage_reduces_pnl(self, simulator: TradeSimulator) -> None:
        """Net P&L = gross P&L - 2 × brokerage_per_side."""
        orb = _orb_candle(1000, 990)
        c1  = _candle(4, 0, 1000, 1010, 999, 1005)
        eod = _candle(9, 45, 1005, 1015, 1004, 1010)

        setup_no_brok = _setup(TradeSide.LONG, 1000, 990, slippage_pct=0.0, brokerage_per_side=0.0)
        setup_brok    = _setup(TradeSide.LONG, 1000, 990, slippage_pct=0.0, brokerage_per_side=20.0)

        res_no   = simulator.simulate(setup_no_brok, [orb, c1, eod])
        res_brok = simulator.simulate(setup_brok,    [orb, c1, eod])

        assert res_no.pnl - res_brok.pnl == pytest.approx(40.0, rel=1e-2)  # 2×20


# ── P&L and R:R tests ─────────────────────────────────────────────────────────

class TestPnl:
    def test_risk_reward_eod_exit(self, simulator: TradeSimulator) -> None:
        """R:R = (exit - entry) / (entry - sl) for a LONG EOD exit."""
        orb = _orb_candle(1000, 950)   # SL = 950, risk = 50
        c1  = _candle(4, 0, 1000, 1015, 999, 1010)
        eod = _candle(9, 45, 1010, 1060, 1009, 1050)  # gain = 1050 - 1000 = 50 → R:R = 1.0

        setup = _setup(TradeSide.LONG, 1000, 950, slippage_pct=0.0, brokerage_per_side=0.0)
        result = simulator.simulate(setup, [orb, c1, eod])

        assert result.risk_reward == pytest.approx(1.0, rel=1e-3)

    def test_losing_trade_has_negative_pnl(self, simulator: TradeSimulator) -> None:
        orb = _orb_candle(1000, 990)
        c1  = _candle(4, 0, 1000, 1005, 999, 1003)
        c2  = _candle(4, 15, 1003, 1004, 985, 986)  # SL=990 hit

        setup = _setup(TradeSide.LONG, 1000, 990, slippage_pct=0.0, brokerage_per_side=0.0)
        result = simulator.simulate(setup, [orb, c1, c2])

        assert result.pnl < 0
        assert result.exit_reason == ExitReason.SL_HIT

    def test_eod_exit_pnl_zero_when_no_movement(self, simulator: TradeSimulator) -> None:
        """When entry price equals EOD exit price, net P&L is 0 (ignoring brokerage)."""
        orb = _orb_candle(1000, 990)
        c1  = _candle(4, 0, 1000, 1005, 999, 1003)  # close 1003 > 1000 → LONG entry
        eod = _candle(9, 45, 1003, 1004, 999, 1000)  # exit at 1000 = entry price

        setup = _setup(TradeSide.LONG, 1000, 990, slippage_pct=0.0, brokerage_per_side=0.0)
        result = simulator.simulate(setup, [orb, c1, eod])

        assert result.pnl == pytest.approx(0.0, abs=0.1)
