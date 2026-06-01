"""
Unit tests for the capital allocation engine.

All pure logic — no MongoDB, no async.
"""

from __future__ import annotations

import pytest

from app.models.portfolio_allocation import AllocationMethod
from app.portfolio.capital_allocator import (
    AllocationInput,
    AllocationResult,
    CapitalAllocator,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _allocator(
    total_capital: float = 1_000_000.0,
    max_pct: float = 0.20,
    fixed_risk_pct: float = 0.01,
    min_capital: float = 5_000.0,
) -> CapitalAllocator:
    return CapitalAllocator(
        total_capital=total_capital,
        max_capital_per_trade_pct=max_pct,
        fixed_risk_pct=fixed_risk_pct,
        min_capital_per_trade=min_capital,
    )


def _inp(
    signal_id: str = "sig1",
    symbol: str = "RELIANCE",
    strategy_id: str = "one_side_orb",
    ranking_score: float = 0.70,
    entry_price: float = 2_500.0,
    stop_loss: float = 2_450.0,
) -> AllocationInput:
    return AllocationInput(
        signal_id=signal_id,
        symbol=symbol,
        strategy_id=strategy_id,
        ranking_score=ranking_score,
        entry_price=entry_price,
        stop_loss=stop_loss,
    )


# ── Equal Weight ──────────────────────────────────────────────────────────────

def test_equal_weight_single_signal():
    allocator = _allocator()
    results = allocator.allocate([_inp()], available_capital=100_000.0, method=AllocationMethod.EQUAL_WEIGHT)
    assert len(results) == 1
    assert results[0].allocated_capital == 100_000.0
    assert results[0].method == AllocationMethod.EQUAL_WEIGHT
    assert results[0].rejection_reason is None


def test_equal_weight_multiple_signals():
    allocator = _allocator()
    inps = [_inp(signal_id=f"s{i}") for i in range(4)]
    results = allocator.allocate(inps, available_capital=100_000.0, method=AllocationMethod.EQUAL_WEIGHT)
    per_trade = 100_000.0 / 4
    for r in results:
        assert r.allocated_capital == per_trade
        assert r.rejection_reason is None


def test_equal_weight_caps_at_max_per_trade():
    # 1 signal, 600k available, max 20% of 1M = 200k
    allocator = _allocator(total_capital=1_000_000.0, max_pct=0.20)
    results = allocator.allocate([_inp()], available_capital=600_000.0, method=AllocationMethod.EQUAL_WEIGHT)
    assert results[0].allocated_capital == 200_000.0


def test_equal_weight_rejects_when_below_minimum():
    # 10 signals on 40k available → 4k each < 5k min
    allocator = _allocator(min_capital=5_000.0)
    inps = [_inp(signal_id=f"s{i}") for i in range(10)]
    results = allocator.allocate(inps, available_capital=40_000.0, method=AllocationMethod.EQUAL_WEIGHT)
    for r in results:
        assert r.allocated_capital == 0.0
        assert r.rejection_reason == "capital_below_minimum"


# ── Score Weighted ────────────────────────────────────────────────────────────

def test_score_weighted_proportional():
    allocator = _allocator()
    inps = [
        _inp(signal_id="a", ranking_score=0.6),
        _inp(signal_id="b", ranking_score=0.4),
    ]
    results = allocator.allocate(inps, available_capital=100_000.0, method=AllocationMethod.SCORE_WEIGHTED)
    # a gets 60%, b gets 40% — subject to max_per_trade cap
    a = next(r for r in results if r.signal_id == "a")
    b = next(r for r in results if r.signal_id == "b")
    assert a.allocated_capital > b.allocated_capital
    assert abs(a.allocated_capital / b.allocated_capital - 1.5) < 0.01


def test_score_weighted_falls_back_to_equal_when_all_zero():
    allocator = _allocator()
    inps = [_inp(signal_id=f"s{i}", ranking_score=0.0) for i in range(3)]
    results = allocator.allocate(inps, available_capital=90_000.0, method=AllocationMethod.SCORE_WEIGHTED)
    # All should get equal share
    capitals = [r.allocated_capital for r in results]
    assert all(c == capitals[0] for c in capitals)


# ── Fixed Risk ────────────────────────────────────────────────────────────────

def test_fixed_risk_basic():
    # risk_pct=1%, total=1M → risk_amount=10k
    # entry=2500, sl=2450 → risk_per_share=50
    # shares = 10000/50 = 200; capital = 200*2500 = 500k → capped at max (200k)
    allocator = _allocator(total_capital=1_000_000.0, max_pct=0.20, fixed_risk_pct=0.01)
    result = allocator.allocate(
        [_inp(entry_price=2_500.0, stop_loss=2_450.0)],
        available_capital=500_000.0,
        method=AllocationMethod.FIXED_RISK,
    )
    assert result[0].allocated_capital == 200_000.0  # capped at 20%


def test_fixed_risk_zero_risk_per_share_rejected():
    allocator = _allocator()
    inp = _inp(entry_price=2_500.0, stop_loss=2_500.0)  # sl == entry → zero risk
    result = allocator.allocate([inp], available_capital=100_000.0, method=AllocationMethod.FIXED_RISK)
    assert result[0].allocated_capital == 0.0
    assert result[0].rejection_reason == "zero_risk_per_share"


def test_fixed_risk_caps_at_available_capital():
    # Tiny available capital should cap the allocation
    allocator = _allocator(total_capital=1_000_000.0, min_capital=1.0)
    result = allocator.allocate(
        [_inp(entry_price=2_500.0, stop_loss=2_490.0)],  # 10 risk, shares=1000, raw=2.5M
        available_capital=8_000.0,
        method=AllocationMethod.FIXED_RISK,
    )
    assert result[0].allocated_capital == 8_000.0


# ── Empty and edge cases ──────────────────────────────────────────────────────

def test_empty_candidates_returns_empty():
    allocator = _allocator()
    assert allocator.allocate([], available_capital=100_000.0, method=AllocationMethod.EQUAL_WEIGHT) == []


def test_allocation_percent_consistent_with_capital():
    allocator = _allocator(total_capital=1_000_000.0)
    result = allocator.allocate([_inp()], available_capital=100_000.0, method=AllocationMethod.EQUAL_WEIGHT)
    r = result[0]
    assert abs(r.allocation_percent - r.allocated_capital / 1_000_000.0) < 1e-5


def test_allocator_properties():
    allocator = _allocator(total_capital=500_000.0, max_pct=0.10, fixed_risk_pct=0.02)
    assert allocator.total_capital == 500_000.0
    assert allocator.max_capital_per_trade == 50_000.0
    assert allocator.fixed_risk_amount == 10_000.0
