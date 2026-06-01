"""
Unit tests for StockAttributionEngine.

Verifies:
  - per-symbol aggregation and ranking
  - top / worst performer selection
  - contribution % calculation
  - consistency score ordering
  - strategy breakdown per symbol
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.analytics.stock_attribution import StockAttributionEngine
from app.schemas.performance import TradingMode


def _utc():
    return datetime(2025, 1, 15, tzinfo=timezone.utc)


def _trade(symbol="RELIANCE", strategy_id="s1", strategy_name="S1", pnl=100.0):
    t = MagicMock()
    t.symbol = symbol
    t.strategy_id = strategy_id
    t.strategy_name = strategy_name
    t.pnl = pnl
    t.trading_date = _utc()
    return t


def _engine(trades=None):
    paper_repo = AsyncMock()
    paper_repo.list_between = AsyncMock(return_value=trades or [])
    bt_run_repo = AsyncMock()
    bt_run_repo.list_runs = AsyncMock(return_value=[])
    bt_trade_repo = AsyncMock()
    bt_trade_repo.get_all_trades_for_run = AsyncMock(return_value=[])
    live_repo = AsyncMock()
    live_repo.get_closed_between = AsyncMock(return_value=[])
    return StockAttributionEngine(
        paper_repo=paper_repo,
        backtest_run_repo=bt_run_repo,
        backtest_trade_repo=bt_trade_repo,
        live_repo=live_repo,
    )


# ── Aggregation ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_trades_returns_empty():
    eng = _engine([])
    results = await eng.compute(date(2025, 1, 1), date(2025, 1, 31))
    assert results == []


@pytest.mark.asyncio
async def test_single_symbol_aggregated():
    trades = [_trade("REL", pnl=200.0), _trade("REL", pnl=100.0), _trade("REL", pnl=-50.0)]
    eng = _engine(trades)
    results = await eng.compute(date(2025, 1, 1), date(2025, 1, 31))
    assert len(results) == 1
    sp = results[0]
    assert sp.symbol == "REL"
    assert sp.total_trades == 3
    assert sp.net_pnl == pytest.approx(250.0, abs=0.01)
    assert sp.wins == 2
    assert sp.win_rate == pytest.approx(2 / 3, abs=0.001)


@pytest.mark.asyncio
async def test_multiple_symbols_separate_rows():
    trades = [_trade("REL", pnl=300.0), _trade("TCS", pnl=100.0), _trade("TCS", pnl=-50.0)]
    eng = _engine(trades)
    results = await eng.compute(date(2025, 1, 1), date(2025, 1, 31))
    assert len(results) == 2
    symbols = {r.symbol for r in results}
    assert "REL" in symbols and "TCS" in symbols


# ── Contribution % ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_contribution_pcts_sum_correctly():
    trades = [
        _trade("A", pnl=300.0),
        _trade("B", pnl=200.0),
        _trade("C", pnl=500.0),
    ]
    eng = _engine(trades)
    results = await eng.compute(date(2025, 1, 1), date(2025, 1, 31))
    total = sum(r.contribution_pct for r in results)
    assert abs(total - 100.0) < 0.01


@pytest.mark.asyncio
async def test_contribution_pct_reflects_share():
    trades = [_trade("A", pnl=750.0), _trade("B", pnl=250.0)]
    eng = _engine(trades)
    results = await eng.compute(date(2025, 1, 1), date(2025, 1, 31), top_n=100)
    a = next(r for r in results if r.symbol == "A")
    b = next(r for r in results if r.symbol == "B")
    assert a.contribution_pct == pytest.approx(75.0, abs=0.01)
    assert b.contribution_pct == pytest.approx(25.0, abs=0.01)


# ── Sorted correctly ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sorted_by_pnl_descending():
    trades = [_trade("C", pnl=100.0), _trade("A", pnl=500.0), _trade("B", pnl=-200.0)]
    eng = _engine(trades)
    results = await eng.compute(date(2025, 1, 1), date(2025, 1, 31))
    pnls = [r.net_pnl for r in results]
    assert pnls == sorted(pnls, reverse=True)


@pytest.mark.asyncio
async def test_top_n_limits_results():
    trades = [_trade(f"SYM{i}", pnl=float(i * 100)) for i in range(20)]
    eng = _engine(trades)
    results = await eng.compute(date(2025, 1, 1), date(2025, 1, 31), top_n=5)
    assert len(results) == 5


# ── Worst performers ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_worst_performers_returns_lowest_pnl():
    trades = [_trade("A", pnl=500.0), _trade("B", pnl=-300.0), _trade("C", pnl=-100.0)]
    eng = _engine(trades)
    worst = await eng.worst_performers(date(2025, 1, 1), date(2025, 1, 31), n=2)
    assert len(worst) == 2
    assert worst[0].net_pnl <= worst[1].net_pnl  # ascending (worst first)


# ── Consistency score ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_high_win_rate_many_trades_has_highest_consistency():
    trades = (
        [_trade("CONSISTENT", pnl=100.0)] * 30        # 30 wins
        + [_trade("LUCKY", pnl=100.0)] * 2            # 2 wins, very few trades
        + [_trade("LOSER", pnl=-100.0)] * 5           # all losses
    )
    eng = _engine(trades)
    results = await eng.compute(date(2025, 1, 1), date(2025, 1, 31), top_n=100)
    consistent = next(r for r in results if r.symbol == "CONSISTENT")
    lucky = next(r for r in results if r.symbol == "LUCKY")
    assert consistent.consistency_score > lucky.consistency_score


# ── Strategy breakdown ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_strategy_breakdown_per_symbol():
    trades = [
        _trade("REL", strategy_id="s1", strategy_name="Strat1", pnl=300.0),
        _trade("REL", strategy_id="s2", strategy_name="Strat2", pnl=200.0),
        _trade("REL", strategy_id="s1", strategy_name="Strat1", pnl=-50.0),
    ]
    eng = _engine(trades)
    results = await eng.compute(date(2025, 1, 1), date(2025, 1, 31))
    sp = results[0]
    assert len(sp.strategy_breakdown) == 2
    ids = {b.strategy_id for b in sp.strategy_breakdown}
    assert ids == {"s1", "s2"}


@pytest.mark.asyncio
async def test_strategy_breakdown_pnl_sums_to_symbol_pnl():
    trades = [
        _trade("REL", strategy_id="s1", pnl=300.0),
        _trade("REL", strategy_id="s2", pnl=200.0),
    ]
    eng = _engine(trades)
    results = await eng.compute(date(2025, 1, 1), date(2025, 1, 31))
    sp = results[0]
    breakdown_total = sum(b.net_pnl for b in sp.strategy_breakdown)
    assert abs(breakdown_total - sp.net_pnl) < 0.01
