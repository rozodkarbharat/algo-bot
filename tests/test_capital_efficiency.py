"""
Unit tests for CapitalEfficiencyEngine.

Verifies:
  - utilization % from portfolio allocations
  - ROAC from trade P&L vs deployed capital
  - idle capital %
  - approval rate from allocation data
  - strategy-level efficiency breakdown
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.analytics.capital_efficiency import CapitalEfficiencyEngine
from app.models.portfolio_allocation import AllocationStatus
from app.schemas.performance import TradingMode


def _utc():
    return datetime(2025, 1, 15, tzinfo=timezone.utc)


def _allocation(status=AllocationStatus.APPROVED, allocated=100_000.0):
    a = MagicMock()
    a.allocation_status = status
    a.allocated_capital = allocated
    return a


def _paper_trade(strategy_id="s1", pnl=500.0, entry_price=2500.0, quantity=10):
    t = MagicMock()
    t.strategy_id = strategy_id
    t.pnl = pnl
    t.entry_price = entry_price
    t.quantity = quantity
    t.trading_date = _utc()
    return t


def _engine(allocations=None, paper_trades=None):
    alloc_repo = AsyncMock()
    alloc_repo.get_for_date_range = AsyncMock(return_value=allocations or [])
    paper_repo = AsyncMock()
    paper_repo.list_between = AsyncMock(return_value=paper_trades or [])
    bt_run_repo = AsyncMock()
    bt_run_repo.list_runs = AsyncMock(return_value=[])
    bt_trade_repo = AsyncMock()
    live_repo = AsyncMock()
    live_repo.get_closed_between = AsyncMock(return_value=[])
    return CapitalEfficiencyEngine(
        allocation_repo=alloc_repo,
        paper_repo=paper_repo,
        backtest_run_repo=bt_run_repo,
        backtest_trade_repo=bt_trade_repo,
        live_repo=live_repo,
    )


# ── Empty data ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_data_returns_zeroes():
    eng = _engine()
    result = await eng.compute(date(2025, 1, 1), date(2025, 1, 31), total_capital=1_000_000.0)
    assert result.total_signals == 0
    assert result.total_allocated == 0.0
    assert result.total_deployed == 0.0
    assert result.roac == 0.0
    assert result.utilization_pct == 0.0
    assert result.idle_capital_pct == 100.0


# ── Utilization % ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_utilization_pct():
    allocs = [_allocation(AllocationStatus.APPROVED, 200_000.0)]
    eng = _engine(allocations=allocs)
    result = await eng.compute(date(2025, 1, 1), date(2025, 1, 31), total_capital=1_000_000.0)
    assert result.utilization_pct == pytest.approx(20.0, abs=0.01)


@pytest.mark.asyncio
async def test_idle_capital_pct_is_complement_of_utilization():
    allocs = [_allocation(AllocationStatus.APPROVED, 300_000.0)]
    eng = _engine(allocations=allocs)
    result = await eng.compute(date(2025, 1, 1), date(2025, 1, 31), total_capital=1_000_000.0)
    assert result.idle_capital_pct == pytest.approx(70.0, abs=0.01)


# ── Approval rate ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approval_rate_calculation():
    allocs = [
        _allocation(AllocationStatus.APPROVED, 100_000.0),
        _allocation(AllocationStatus.APPROVED, 100_000.0),
        _allocation(AllocationStatus.REJECTED, 0.0),
        _allocation(AllocationStatus.REJECTED, 0.0),
    ]
    eng = _engine(allocations=allocs)
    result = await eng.compute(date(2025, 1, 1), date(2025, 1, 31), total_capital=1_000_000.0)
    assert result.total_signals == 4
    assert result.approved_signals == 2
    assert result.rejected_signals == 2
    assert result.approval_rate == pytest.approx(0.5, abs=0.001)


# ── ROAC ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_roac_basic():
    # capital_used = 2500 * 10 = 25_000; pnl = 500
    trades = [_paper_trade(pnl=500.0, entry_price=2500.0, quantity=10)]
    eng = _engine(paper_trades=trades)
    result = await eng.compute(date(2025, 1, 1), date(2025, 1, 31), total_capital=1_000_000.0)
    assert result.total_deployed == pytest.approx(25_000.0, abs=0.01)
    assert result.total_net_pnl == pytest.approx(500.0, abs=0.01)
    assert result.roac == pytest.approx(500.0 / 25_000.0, abs=1e-5)


@pytest.mark.asyncio
async def test_roac_negative_pnl():
    trades = [_paper_trade(pnl=-1000.0, entry_price=2500.0, quantity=10)]
    eng = _engine(paper_trades=trades)
    result = await eng.compute(date(2025, 1, 1), date(2025, 1, 31), total_capital=1_000_000.0)
    assert result.roac < 0


# ── Strategy efficiency breakdown ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_strategy_efficiency_breakdown():
    trades = [
        _paper_trade(strategy_id="s1", pnl=300.0, entry_price=1000.0, quantity=5),
        _paper_trade(strategy_id="s2", pnl=700.0, entry_price=2000.0, quantity=3),
    ]
    eng = _engine(paper_trades=trades)
    result = await eng.compute(date(2025, 1, 1), date(2025, 1, 31), total_capital=1_000_000.0)
    assert "s1" in result.strategy_efficiency
    assert "s2" in result.strategy_efficiency
    assert result.strategy_efficiency["s1"]["pnl"] == pytest.approx(300.0)
    assert result.strategy_efficiency["s2"]["deployed"] == pytest.approx(6000.0)


# ── Deployment efficiency ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deployment_efficiency_pct():
    allocs = [_allocation(AllocationStatus.APPROVED, 100_000.0)]
    trades = [_paper_trade(pnl=500.0, entry_price=2500.0, quantity=10)]  # 25k deployed
    eng = _engine(allocations=allocs, paper_trades=trades)
    result = await eng.compute(date(2025, 1, 1), date(2025, 1, 31), total_capital=1_000_000.0)
    # deployed 25k of 100k allocated → 25%
    assert result.deployment_efficiency_pct == pytest.approx(25.0, abs=0.01)


# ── pnl_per_rupee ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pnl_per_rupee_equals_roac():
    trades = [_paper_trade(pnl=250.0, entry_price=2500.0, quantity=10)]
    eng = _engine(paper_trades=trades)
    result = await eng.compute(date(2025, 1, 1), date(2025, 1, 31), total_capital=1_000_000.0)
    assert result.pnl_per_rupee_invested == result.roac


# ── Strategy comparison ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_strategy_efficiency_roac_computed():
    trades = [
        _paper_trade(strategy_id="good", pnl=5_000.0, entry_price=2500.0, quantity=10),
        _paper_trade(strategy_id="bad", pnl=-1_000.0, entry_price=2500.0, quantity=10),
    ]
    eng = _engine(paper_trades=trades)
    result = await eng.compute(date(2025, 1, 1), date(2025, 1, 31), total_capital=1_000_000.0)
    assert result.strategy_efficiency["good"]["roac"] > 0
    assert result.strategy_efficiency["bad"]["roac"] < 0
