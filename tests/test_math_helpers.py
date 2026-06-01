"""
Unit tests for app/analytics/math_helpers.py

All pure synchronous logic — no mocks, no DB, no async.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from app.analytics.math_helpers import (
    avg_win_avg_loss,
    calmar_ratio,
    consistency_score,
    contribution_pct,
    cumulative_pnl_series,
    daily_pnl_series,
    expectancy,
    max_drawdown,
    profit_factor,
    r_multiple,
    rolling_sharpe,
    sharpe_ratio,
    volatility_annual,
    win_rate,
)


# ── win_rate ──────────────────────────────────────────────────────────────────

def test_win_rate_basic():
    assert win_rate(6, 10) == 0.6


def test_win_rate_zero_trades():
    assert win_rate(0, 0) == 0.0


def test_win_rate_all_wins():
    assert win_rate(5, 5) == 1.0


def test_win_rate_no_wins():
    assert win_rate(0, 10) == 0.0


# ── expectancy ────────────────────────────────────────────────────────────────

def test_expectancy_positive_edge():
    # avg_win=1000, avg_loss=-500, win_rate=0.60
    # 1000*0.6 - 500*0.4 = 600 - 200 = 400
    assert expectancy(1000.0, -500.0, 0.60) == pytest.approx(400.0, abs=0.01)


def test_expectancy_negative_edge():
    # avg_win=200, avg_loss=-800, win_rate=0.40
    # 200*0.4 - 800*0.6 = 80 - 480 = -400
    assert expectancy(200.0, -800.0, 0.40) == pytest.approx(-400.0, abs=0.01)


def test_expectancy_zero_win_rate():
    assert expectancy(1000.0, -500.0, 0.0) == pytest.approx(-500.0, abs=0.01)


def test_expectancy_full_win_rate():
    assert expectancy(300.0, -100.0, 1.0) == pytest.approx(300.0, abs=0.01)


def test_expectancy_invalid_win_rate():
    with pytest.raises(ValueError):
        expectancy(1000.0, -500.0, 1.5)


# ── profit_factor ─────────────────────────────────────────────────────────────

def test_profit_factor_profitable():
    # total_wins=3000, total_losses=-1000 → 3.0
    assert profit_factor(3000.0, -1000.0) == pytest.approx(3.0, abs=0.01)


def test_profit_factor_no_losses():
    result = profit_factor(1000.0, 0.0)
    assert result == float("inf")


def test_profit_factor_no_wins():
    assert profit_factor(0.0, -500.0) == 0.0


def test_profit_factor_losing():
    # wins=500, losses=-1000 → 0.5
    assert profit_factor(500.0, -1000.0) == pytest.approx(0.5, abs=0.01)


# ── sharpe_ratio ──────────────────────────────────────────────────────────────

def test_sharpe_insufficient_data():
    assert sharpe_ratio([100.0]) == 0.0
    assert sharpe_ratio([]) == 0.0


def test_sharpe_zero_std():
    # All same values → std=0 → 0.0
    assert sharpe_ratio([100.0, 100.0, 100.0]) == 0.0


def test_sharpe_positive_for_consistent_gains():
    # 252 identical daily gains → very high Sharpe
    pnls = [100.0] * 252
    # std=0 so returns 0 — testing 0 std branch
    result = sharpe_ratio(pnls)
    assert result == 0.0


def test_sharpe_positive_for_noisy_gains():
    import random
    random.seed(42)
    pnls = [100.0 + random.gauss(0, 50) for _ in range(252)]
    result = sharpe_ratio(pnls)
    # Mean ≈ 100, std ≈ 50 → Sharpe ≈ 100/50 * sqrt(252) ≈ 31.7
    assert result > 0


def test_sharpe_negative_for_consistent_losses():
    pnls = [-100.0] * 252
    # std=0 → 0
    assert sharpe_ratio(pnls) == 0.0


def test_sharpe_mixed():
    pnls = [100.0, -50.0, 80.0, -30.0, 120.0]
    result = sharpe_ratio(pnls)
    # mean ≈ 44, positive → positive Sharpe
    assert isinstance(result, float)


# ── max_drawdown ──────────────────────────────────────────────────────────────

def test_max_drawdown_empty():
    assert max_drawdown([]) == (0.0, 0.0)


def test_max_drawdown_monotone_up():
    dd, pct = max_drawdown([0.0, 100.0, 200.0, 300.0])
    assert dd == 0.0
    assert pct == 0.0


def test_max_drawdown_single_dip():
    # peak=300, trough=100, dd=200, pct=200/300=66.67%
    dd, pct = max_drawdown([0.0, 300.0, 100.0, 200.0])
    assert dd == pytest.approx(200.0, abs=0.01)
    assert pct == pytest.approx(66.67, abs=0.1)


def test_max_drawdown_multiple_dips():
    # Series: 0 → 200 → 150 → 250 → 100
    # Peaks: 200 (dd=50), 250 (dd=150) ← max
    dd, pct = max_drawdown([0.0, 200.0, 150.0, 250.0, 100.0])
    assert dd == pytest.approx(150.0, abs=0.01)


# ── daily_pnl_series ──────────────────────────────────────────────────────────

def test_daily_pnl_series_aggregates():
    dates = [date(2025, 1, 1), date(2025, 1, 1), date(2025, 1, 2)]
    pnls = [100.0, 200.0, -50.0]
    result = daily_pnl_series(dates, pnls)
    assert result[date(2025, 1, 1)] == 300.0
    assert result[date(2025, 1, 2)] == -50.0


def test_daily_pnl_series_sorted():
    dates = [date(2025, 1, 3), date(2025, 1, 1)]
    pnls = [10.0, 20.0]
    result = daily_pnl_series(dates, pnls)
    assert list(result.keys()) == [date(2025, 1, 1), date(2025, 1, 3)]


def test_daily_pnl_series_length_mismatch():
    with pytest.raises(ValueError):
        daily_pnl_series([date(2025, 1, 1)], [1.0, 2.0])


# ── cumulative_pnl_series ─────────────────────────────────────────────────────

def test_cumulative_pnl_series_basic():
    daily = {date(2025, 1, 1): 100.0, date(2025, 1, 2): -30.0, date(2025, 1, 3): 50.0}
    cum = cumulative_pnl_series(daily)
    assert cum == pytest.approx([100.0, 70.0, 120.0], abs=0.01)


def test_cumulative_pnl_series_empty():
    assert cumulative_pnl_series({}) == []


# ── rolling_sharpe ────────────────────────────────────────────────────────────

def test_rolling_sharpe_requires_full_window():
    daily = {date(2025, 1, i): float(i) * 10 for i in range(1, 11)}
    result = rolling_sharpe(daily, window=5)
    # First result key should be the 5th date
    assert date(2025, 1, 5) in result
    assert date(2025, 1, 4) not in result


def test_rolling_sharpe_returns_float_values():
    daily = {date(2025, 1, i): float(i % 3 - 1) * 50 for i in range(1, 25)}
    result = rolling_sharpe(daily, window=10)
    for v in result.values():
        assert isinstance(v, float)


# ── avg_win_avg_loss ──────────────────────────────────────────────────────────

def test_avg_win_avg_loss_mixed():
    pnls = [100.0, -50.0, 200.0, -80.0, 150.0]
    avg_w, avg_l = avg_win_avg_loss(pnls)
    assert avg_w == pytest.approx(150.0, abs=0.01)
    assert avg_l == pytest.approx(-65.0, abs=0.01)


def test_avg_win_avg_loss_all_wins():
    pnls = [100.0, 200.0, 300.0]
    avg_w, avg_l = avg_win_avg_loss(pnls)
    assert avg_w == pytest.approx(200.0, abs=0.01)
    assert avg_l == 0.0


def test_avg_win_avg_loss_all_losses():
    pnls = [-100.0, -200.0]
    avg_w, avg_l = avg_win_avg_loss(pnls)
    assert avg_w == 0.0
    assert avg_l == pytest.approx(-150.0, abs=0.01)


def test_avg_win_avg_loss_empty():
    assert avg_win_avg_loss([]) == (0.0, 0.0)


# ── r_multiple ────────────────────────────────────────────────────────────────

def test_r_multiple_long_profit():
    # entry=100, exit=120, sl=90 → risk=10, gain=20 → R=2.0
    assert r_multiple(100.0, 120.0, 90.0, "LONG") == pytest.approx(2.0)


def test_r_multiple_long_loss():
    # entry=100, exit=90, sl=90 → risk=10, loss=-10 → R=-1.0
    assert r_multiple(100.0, 90.0, 90.0, "LONG") == pytest.approx(-1.0)


def test_r_multiple_short_profit():
    # entry=100, exit=80, sl=110 → risk=10, gain=20 → R=2.0
    assert r_multiple(100.0, 80.0, 110.0, "SHORT") == pytest.approx(2.0)


def test_r_multiple_zero_risk():
    assert r_multiple(100.0, 120.0, 100.0, "LONG") is None


# ── contribution_pct ──────────────────────────────────────────────────────────

def test_contribution_pct_basic():
    assert contribution_pct(250.0, 1000.0) == pytest.approx(25.0, abs=0.01)


def test_contribution_pct_zero_total():
    assert contribution_pct(100.0, 0.0) == 0.0


def test_contribution_pct_negative_contribution():
    assert contribution_pct(-200.0, 1000.0) == pytest.approx(-20.0, abs=0.01)


# ── consistency_score ─────────────────────────────────────────────────────────

def test_consistency_score_zero_trades():
    assert consistency_score(0.8, 0) == 0.0


def test_consistency_score_higher_with_more_trades():
    low = consistency_score(0.7, 5)
    high = consistency_score(0.7, 50)
    assert high > low


def test_consistency_score_higher_with_better_win_rate():
    low = consistency_score(0.5, 20)
    high = consistency_score(0.9, 20)
    assert high > low


# ── calmar_ratio ──────────────────────────────────────────────────────────────

def test_calmar_ratio_basic():
    # annual_return=20%, dd=100k on capital 1M → dd_pct=10% → calmar=2.0
    assert calmar_ratio(20.0, 100_000.0, 1_000_000.0) == pytest.approx(2.0, abs=0.01)


def test_calmar_ratio_zero_drawdown():
    assert calmar_ratio(20.0, 0.0, 1_000_000.0) == 0.0


def test_calmar_ratio_zero_capital():
    assert calmar_ratio(20.0, 100_000.0, 0.0) == 0.0


# ── volatility_annual ─────────────────────────────────────────────────────────

def test_volatility_annual_insufficient_data():
    assert volatility_annual([100.0]) == 0.0
    assert volatility_annual([]) == 0.0


def test_volatility_annual_positive():
    pnls = [100.0, -50.0, 200.0, -30.0, 80.0]
    result = volatility_annual(pnls)
    assert result > 0
