"""
Unit tests for StrategyAttributionEngine.

All repos are mocked — no DB, no async I/O.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.analytics.strategy_attribution import StrategyAttributionEngine
from app.schemas.performance import TradingMode


def _utc(y=2025, m=1, d=1):
    return datetime(y, m, d, tzinfo=timezone.utc)


def _paper_trade(
    strategy_id="one_side_orb",
    strategy_name="One-Side ORB",
    symbol="RELIANCE",
    pnl=500.0,
    entry_price=2500.0,
    quantity=10,
    brokerage=20.0,
    trading_date_obj=None,
):
    t = MagicMock()
    t.strategy_id = strategy_id
    t.strategy_name = strategy_name
    t.symbol = symbol
    t.pnl = pnl
    t.entry_price = entry_price
    t.quantity = quantity
    t.brokerage = brokerage
    t.trading_date = trading_date_obj or _utc()
    return t


def _make_engine(paper_trades=None, bt_runs=None, bt_trades=None, live_positions=None):
    paper_repo = AsyncMock()
    paper_repo.list_between = AsyncMock(return_value=paper_trades or [])

    bt_run_repo = AsyncMock()
    bt_run_repo.list_runs = AsyncMock(return_value=bt_runs or [])

    bt_trade_repo = AsyncMock()
    bt_trade_repo.get_all_trades_for_run = AsyncMock(return_value=bt_trades or [])

    bt_metrics_repo = AsyncMock()

    live_repo = AsyncMock()
    live_repo.get_closed_between = AsyncMock(return_value=live_positions or [])

    return StrategyAttributionEngine(
        paper_repo=paper_repo,
        backtest_run_repo=bt_run_repo,
        backtest_trade_repo=bt_trade_repo,
        backtest_metrics_repo=bt_metrics_repo,
        live_repo=live_repo,
    )


# ── Core computation ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_trades_returns_empty():
    engine = _make_engine()
    results = await engine.compute(date(2025, 1, 1), date(2025, 1, 31), TradingMode.PAPER)
    assert results == []


@pytest.mark.asyncio
async def test_single_strategy_paper_mode():
    trades = [_paper_trade(pnl=500.0), _paper_trade(pnl=-200.0), _paper_trade(pnl=300.0)]
    engine = _make_engine(paper_trades=trades)
    results = await engine.compute(date(2025, 1, 1), date(2025, 1, 31), TradingMode.PAPER)
    assert len(results) == 1
    sp = results[0]
    assert sp.strategy_id == "one_side_orb"
    assert sp.total_trades == 3
    assert sp.wins == 2
    assert sp.losses == 1
    assert sp.net_pnl == pytest.approx(600.0, abs=0.01)


@pytest.mark.asyncio
async def test_win_rate_computed_correctly():
    trades = [_paper_trade(pnl=p) for p in [100.0, 200.0, -50.0, 150.0, -100.0]]
    engine = _make_engine(paper_trades=trades)
    results = await engine.compute(date(2025, 1, 1), date(2025, 1, 31), TradingMode.PAPER)
    sp = results[0]
    assert sp.total_trades == 5
    assert sp.wins == 3
    assert sp.win_rate == pytest.approx(0.6, abs=0.001)


@pytest.mark.asyncio
async def test_multiple_strategies_returns_one_per_strategy():
    trades = [
        _paper_trade(strategy_id="s1", strategy_name="Strat1", pnl=400.0),
        _paper_trade(strategy_id="s1", strategy_name="Strat1", pnl=-100.0),
        _paper_trade(strategy_id="s2", strategy_name="Strat2", pnl=700.0),
    ]
    engine = _make_engine(paper_trades=trades)
    results = await engine.compute(date(2025, 1, 1), date(2025, 1, 31), TradingMode.PAPER)
    assert len(results) == 2
    ids = {r.strategy_id for r in results}
    assert ids == {"s1", "s2"}


@pytest.mark.asyncio
async def test_results_sorted_by_pnl_descending():
    trades = [
        _paper_trade(strategy_id="low", pnl=-100.0),
        _paper_trade(strategy_id="high", pnl=500.0),
        _paper_trade(strategy_id="mid", pnl=200.0),
    ]
    engine = _make_engine(paper_trades=trades)
    results = await engine.compute(date(2025, 1, 1), date(2025, 1, 31), TradingMode.PAPER)
    pnls = [r.net_pnl for r in results]
    assert pnls == sorted(pnls, reverse=True)


@pytest.mark.asyncio
async def test_strategy_id_filter():
    trades = [
        _paper_trade(strategy_id="s1", pnl=300.0),
        _paper_trade(strategy_id="s2", pnl=700.0),
    ]
    engine = _make_engine(paper_trades=trades)
    results = await engine.compute(
        date(2025, 1, 1), date(2025, 1, 31), TradingMode.PAPER, strategy_id="s1"
    )
    assert len(results) == 1
    assert results[0].strategy_id == "s1"


@pytest.mark.asyncio
async def test_gross_pnl_higher_than_net():
    trades = [_paper_trade(pnl=500.0, brokerage=20.0)]
    engine = _make_engine(paper_trades=trades)
    results = await engine.compute(date(2025, 1, 1), date(2025, 1, 31), TradingMode.PAPER)
    sp = results[0]
    # gross = pnl + 2*brokerage = 500 + 40 = 540
    assert sp.gross_pnl >= sp.net_pnl


@pytest.mark.asyncio
async def test_expectancy_computed():
    trades = [_paper_trade(pnl=p) for p in [100.0, -50.0, 200.0, -100.0]]
    engine = _make_engine(paper_trades=trades)
    results = await engine.compute(date(2025, 1, 1), date(2025, 1, 31), TradingMode.PAPER)
    sp = results[0]
    assert isinstance(sp.expectancy, float)


@pytest.mark.asyncio
async def test_daily_pnl_series_populated():
    trades = [_paper_trade(pnl=100.0)]
    engine = _make_engine(paper_trades=trades)
    results = await engine.compute(date(2025, 1, 1), date(2025, 1, 31), TradingMode.PAPER)
    sp = results[0]
    assert len(sp.daily_pnl) > 0


@pytest.mark.asyncio
async def test_profit_factor_all_wins():
    trades = [_paper_trade(pnl=p) for p in [100.0, 200.0, 300.0]]
    engine = _make_engine(paper_trades=trades)
    results = await engine.compute(date(2025, 1, 1), date(2025, 1, 31), TradingMode.PAPER)
    sp = results[0]
    assert sp.profit_factor == float("inf")


@pytest.mark.asyncio
async def test_live_mode_uses_live_positions():
    live_pos = MagicMock()
    live_pos.strategy_id = None  # will default to "live"
    live_pos.strategy_name = None
    live_pos.realized_pnl = 300.0
    live_pos.average_price = 2500.0
    live_pos.quantity = 10
    live_pos.trading_date = _utc()

    engine = _make_engine(live_positions=[live_pos])
    results = await engine.compute(date(2025, 1, 1), date(2025, 1, 31), TradingMode.LIVE)
    assert len(results) == 1
    assert results[0].net_pnl == pytest.approx(300.0)
