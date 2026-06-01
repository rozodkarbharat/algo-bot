"""
Unit tests for the MetricsEngine.

Tests verify:
  - Win rate, SL hit rate, breakout success rate calculations
  - P&L aggregates: total, avg, avg_win, avg_loss
  - Profit factor and expectancy
  - Max drawdown computation
  - Consecutive wins/losses
  - Per-symbol and time breakdowns

All tests are pure Python — no database, no I/O.

Run with:
    pytest tests/test_metrics_engine.py -v
"""

from datetime import datetime, timezone

import pytest

from app.models.backtest_trade import ExitReason, TradeSide
from app.strategy.metrics_engine import MetricsEngine, MetricsResult
from app.strategy.trade_simulator import SimulatedTrade


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trade(
    symbol: str = "RELIANCE",
    pnl: float = 100.0,
    exit_reason: ExitReason = ExitReason.EOD_EXIT,
    side: TradeSide = TradeSide.LONG,
    entry_time: datetime = datetime(2024, 6, 3, 4, 0, tzinfo=timezone.utc),
    capital: float = 100_000.0,
    rr: float | None = None,
) -> SimulatedTrade:
    return SimulatedTrade(
        symbol=symbol,
        trade_side=side,
        breakout_side="UP" if side == TradeSide.LONG else "DOWN",
        orb_high=1000.0,
        orb_low=990.0,
        probability_score=0.75,
        entry_time=entry_time,
        entry_price=1000.0,
        stop_loss=990.0,
        exit_time=datetime(2024, 6, 3, 9, 45, tzinfo=timezone.utc),
        exit_price=1000.0 + pnl / 100,   # approximate
        exit_reason=exit_reason,
        quantity=100,
        capital_used=capital,
        pnl=pnl,
        pnl_percent=pnl / capital * 100,
        risk_reward=rr,
        metadata={},
    )


def _no_breakout(symbol: str = "RELIANCE") -> SimulatedTrade:
    return SimulatedTrade(
        symbol=symbol,
        trade_side=TradeSide.LONG,
        breakout_side="UP",
        orb_high=1000.0,
        orb_low=990.0,
        probability_score=0.72,
        entry_time=None,
        entry_price=None,
        stop_loss=990.0,
        exit_time=None,
        exit_price=None,
        exit_reason=ExitReason.NO_BREAKOUT,
        quantity=0,
        capital_used=0.0,
        pnl=0.0,
        pnl_percent=0.0,
        risk_reward=None,
        metadata={},
    )


@pytest.fixture
def engine() -> MetricsEngine:
    return MetricsEngine()


# ── Empty / edge cases ────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_no_trades_returns_empty_metrics(self, engine: MetricsEngine) -> None:
        metrics = engine.calculate("run-001", [], total_candidate_days=0)
        assert metrics.total_trades == 0
        assert metrics.win_rate == 0.0
        assert metrics.total_pnl == 0.0

    def test_only_no_breakout_trades(self, engine: MetricsEngine) -> None:
        trades = [_no_breakout() for _ in range(5)]
        metrics = engine.calculate("run-001", trades, total_candidate_days=5)
        assert metrics.total_trades == 0
        assert metrics.no_entry_days == 5
        assert metrics.breakout_success_rate == 0.0


# ── Win rate ──────────────────────────────────────────────────────────────────

class TestWinRate:
    def test_win_rate_all_winners(self, engine: MetricsEngine) -> None:
        trades = [_trade(pnl=100) for _ in range(4)]
        metrics = engine.calculate("run-001", trades, total_candidate_days=4)
        assert metrics.win_rate == pytest.approx(1.0)

    def test_win_rate_mixed(self, engine: MetricsEngine) -> None:
        trades = [_trade(pnl=100), _trade(pnl=-50), _trade(pnl=200), _trade(pnl=-30)]
        metrics = engine.calculate("run-001", trades, total_candidate_days=4)
        assert metrics.win_rate == pytest.approx(0.5)

    def test_sl_hit_rate(self, engine: MetricsEngine) -> None:
        trades = [
            _trade(pnl=-50, exit_reason=ExitReason.SL_HIT),
            _trade(pnl=100, exit_reason=ExitReason.EOD_EXIT),
            _trade(pnl=-30, exit_reason=ExitReason.SL_HIT),
            _trade(pnl=150, exit_reason=ExitReason.EOD_EXIT),
        ]
        metrics = engine.calculate("run-001", trades, total_candidate_days=6)
        assert metrics.sl_hit_rate == pytest.approx(0.5)


# ── P&L aggregates ────────────────────────────────────────────────────────────

class TestPnlAggregates:
    def test_total_pnl(self, engine: MetricsEngine) -> None:
        trades = [_trade(pnl=100), _trade(pnl=-40), _trade(pnl=200)]
        metrics = engine.calculate("r", trades, total_candidate_days=3)
        assert metrics.total_pnl == pytest.approx(260.0)

    def test_avg_win_and_avg_loss(self, engine: MetricsEngine) -> None:
        trades = [
            _trade(pnl=100), _trade(pnl=200),
            _trade(pnl=-50), _trade(pnl=-150),
        ]
        metrics = engine.calculate("r", trades, total_candidate_days=4)
        assert metrics.avg_win  == pytest.approx(150.0)
        assert metrics.avg_loss == pytest.approx(-100.0)

    def test_max_win_and_max_loss(self, engine: MetricsEngine) -> None:
        trades = [_trade(pnl=300), _trade(pnl=50), _trade(pnl=-200), _trade(pnl=-20)]
        metrics = engine.calculate("r", trades, total_candidate_days=4)
        assert metrics.max_win  == pytest.approx(300.0)
        assert metrics.max_loss == pytest.approx(-200.0)


# ── Risk metrics ──────────────────────────────────────────────────────────────

class TestRiskMetrics:
    def test_profit_factor_profitable(self, engine: MetricsEngine) -> None:
        trades = [_trade(pnl=300), _trade(pnl=100), _trade(pnl=-100)]
        metrics = engine.calculate("r", trades, total_candidate_days=3)
        # gross_profit=400, gross_loss=100 → PF=4.0
        assert metrics.profit_factor == pytest.approx(4.0)

    def test_profit_factor_no_losses(self, engine: MetricsEngine) -> None:
        trades = [_trade(pnl=100), _trade(pnl=200)]
        metrics = engine.calculate("r", trades, total_candidate_days=2)
        assert metrics.profit_factor == 0.0   # convention: 0.0 when no losses

    def test_expectancy(self, engine: MetricsEngine) -> None:
        # 2 wins of 100, 2 losses of -50
        # win_rate=0.5, avg_win=100, avg_loss=-50
        # expectancy = 0.5*100 - 0.5*50 = 50 - 25 = 25
        trades = [_trade(pnl=100), _trade(pnl=100), _trade(pnl=-50), _trade(pnl=-50)]
        metrics = engine.calculate("r", trades, total_candidate_days=4)
        assert metrics.expectancy == pytest.approx(25.0)

    def test_max_drawdown_simple(self, engine: MetricsEngine) -> None:
        # Equity curve: 100, 200, 100, 50 → peak=200, trough=50 → DD=150
        trades = [
            _trade(pnl=100),   # equity=100
            _trade(pnl=100),   # equity=200  ← peak
            _trade(pnl=-100),  # equity=100
            _trade(pnl=-50),   # equity=50   ← trough
        ]
        metrics = engine.calculate("r", trades, total_candidate_days=4)
        assert metrics.max_drawdown == pytest.approx(150.0)
        assert metrics.max_drawdown_percent == pytest.approx(75.0)  # 150/200


# ── Consecutive stats ─────────────────────────────────────────────────────────

class TestConsecutive:
    def test_max_consecutive_wins(self, engine: MetricsEngine) -> None:
        trades = [
            _trade(pnl=100), _trade(pnl=50), _trade(pnl=30),   # 3 wins
            _trade(pnl=-50),
            _trade(pnl=100), _trade(pnl=80),                    # 2 wins
        ]
        metrics = engine.calculate("r", trades, total_candidate_days=6)
        assert metrics.max_consecutive_wins == 3

    def test_max_consecutive_losses(self, engine: MetricsEngine) -> None:
        trades = [
            _trade(pnl=100),
            _trade(pnl=-50), _trade(pnl=-40), _trade(pnl=-30),  # 3 losses
            _trade(pnl=100),
        ]
        metrics = engine.calculate("r", trades, total_candidate_days=5)
        assert metrics.max_consecutive_losses == 3


# ── Per-symbol and time breakdowns ────────────────────────────────────────────

class TestBreakdowns:
    def test_per_symbol_metrics(self, engine: MetricsEngine) -> None:
        trades = [
            _trade("RELIANCE", pnl=100),
            _trade("RELIANCE", pnl=-50),
            _trade("TCS", pnl=200),
        ]
        metrics = engine.calculate("r", trades, total_candidate_days=3)
        assert "RELIANCE" in metrics.per_symbol_metrics
        assert "TCS" in metrics.per_symbol_metrics
        assert metrics.per_symbol_metrics["RELIANCE"]["total"] == 2
        assert metrics.per_symbol_metrics["TCS"]["total"] == 1
        assert metrics.per_symbol_metrics["TCS"]["pnl"] == pytest.approx(200.0)

    def test_daily_pnl_aggregation(self, engine: MetricsEngine) -> None:
        # Two trades on the same day (2024-06-03 IST) and one on another day
        day1 = datetime(2024, 6, 3, 4, 0, tzinfo=timezone.utc)  # 9:30 IST
        day2 = datetime(2024, 6, 4, 4, 0, tzinfo=timezone.utc)
        trades = [
            _trade(pnl=100, entry_time=day1),
            _trade(pnl=150, entry_time=day1),
            _trade(pnl=-50, entry_time=day2),
        ]
        metrics = engine.calculate("r", trades, total_candidate_days=3)
        assert "2024-06-03" in metrics.daily_pnl
        assert metrics.daily_pnl["2024-06-03"] == pytest.approx(250.0)
        assert "2024-06-04" in metrics.daily_pnl
        assert metrics.daily_pnl["2024-06-04"] == pytest.approx(-50.0)

    def test_breakout_success_rate(self, engine: MetricsEngine) -> None:
        # 3 executed, 2 no-breakout → success_rate = 3/5 = 0.6
        executed = [_trade(pnl=100) for _ in range(3)]
        no_entry = [_no_breakout() for _ in range(2)]
        metrics = engine.calculate("r", executed + no_entry, total_candidate_days=5)
        assert metrics.breakout_success_rate == pytest.approx(0.6)
