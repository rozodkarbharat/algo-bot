"""
Unit tests for the paper execution engine.

Pure Python — no database, no broker, no I/O. Verifies:
  - LONG / SHORT entry slippage direction
  - Exit slippage direction (always adverse)
  - Quantity sizing under capital constraint
  - PaperFill propagates signal metadata
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.live.signal_engine import GeneratedSignal
from app.models.live_signal import LiveBreakoutSide, LiveSignalType
from app.models.paper_position import PaperTradeSide
from app.paper_trading.paper_execution_engine import PaperExecutionEngine


def _signal(
    signal_type: LiveSignalType,
    entry_price: float = 100.0,
    stop_loss: float = 98.0,
) -> GeneratedSignal:
    return GeneratedSignal(
        symbol="RELIANCE",
        trading_date=datetime(2024, 6, 3, tzinfo=timezone.utc).date(),
        signal_type=signal_type,
        breakout_side=LiveBreakoutSide.UP if signal_type is LiveSignalType.BUY else LiveBreakoutSide.DOWN,
        entry_price=entry_price,
        stop_loss=stop_loss,
        first_candle_high=101.0,
        first_candle_low=99.0,
        orb_range_percent=2.0,
        breakout_time=datetime(2024, 6, 3, 4, 0, 0, tzinfo=timezone.utc),
        probability_score=0.7,
    )


class TestSlippage:
    def test_long_entry_slippage_increases_price(self) -> None:
        eng = PaperExecutionEngine(slippage_pct=0.1, brokerage_per_side=0.0)
        adj = eng.apply_entry_slippage(100.0, PaperTradeSide.LONG)
        assert adj == pytest.approx(100.1)

    def test_short_entry_slippage_decreases_price(self) -> None:
        eng = PaperExecutionEngine(slippage_pct=0.1, brokerage_per_side=0.0)
        adj = eng.apply_entry_slippage(100.0, PaperTradeSide.SHORT)
        assert adj == pytest.approx(99.9)

    def test_long_exit_slippage_decreases_price(self) -> None:
        eng = PaperExecutionEngine(slippage_pct=0.2)
        adj = eng.apply_exit_slippage(50.0, PaperTradeSide.LONG)
        assert adj == pytest.approx(49.9)

    def test_short_exit_slippage_increases_price(self) -> None:
        eng = PaperExecutionEngine(slippage_pct=0.2)
        adj = eng.apply_exit_slippage(50.0, PaperTradeSide.SHORT)
        assert adj == pytest.approx(50.1)


class TestSizing:
    def test_size_quantity_uses_capital_per_trade_floor(self) -> None:
        eng = PaperExecutionEngine(capital_per_trade=100_000.0)
        assert eng.size_quantity(150.0) == 666  # floor(100000 / 150)

    def test_size_quantity_caps_at_available_capital(self) -> None:
        eng = PaperExecutionEngine(capital_per_trade=100_000.0)
        assert eng.size_quantity(150.0, available_capital=10_000.0) == 66

    def test_size_quantity_zero_when_capital_insufficient(self) -> None:
        eng = PaperExecutionEngine(capital_per_trade=100.0)
        assert eng.size_quantity(150.0, available_capital=50.0) == 0


class TestSimulateFill:
    def test_long_fill_has_long_side(self) -> None:
        eng = PaperExecutionEngine(
            slippage_pct=0.0, brokerage_per_side=20.0, capital_per_trade=10_000.0
        )
        signal = _signal(LiveSignalType.BUY, entry_price=100.0)
        fill = eng.simulate_fill(signal, trading_dt_utc=datetime(2024, 6, 3, tzinfo=timezone.utc))
        assert fill.trade_side is PaperTradeSide.LONG
        assert fill.filled_quantity == 100  # 10_000 / 100
        assert fill.entry_price == 100.0
        assert fill.entry_brokerage == 20.0
        assert fill.capital_used == pytest.approx(10_000.0)

    def test_short_fill_has_short_side(self) -> None:
        eng = PaperExecutionEngine(slippage_pct=0.0, capital_per_trade=5_000.0)
        signal = _signal(LiveSignalType.SELL, entry_price=50.0, stop_loss=52.0)
        fill = eng.simulate_fill(signal, trading_dt_utc=datetime(2024, 6, 3, tzinfo=timezone.utc))
        assert fill.trade_side is PaperTradeSide.SHORT
        assert fill.filled_quantity == 100  # 5_000 / 50
        assert fill.stop_loss == 52.0

    def test_metadata_carries_signal_context(self) -> None:
        eng = PaperExecutionEngine(slippage_pct=0.0)
        signal = _signal(LiveSignalType.BUY)
        fill = eng.simulate_fill(signal, trading_dt_utc=datetime(2024, 6, 3, tzinfo=timezone.utc))
        assert fill.metadata["orb_high"] == 101.0
        assert fill.metadata["orb_low"] == 99.0
        assert fill.metadata["probability_score"] == 0.7
