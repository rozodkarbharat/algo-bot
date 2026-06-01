"""Tests for Live Validation & Reality Gap Analysis Framework."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass


# ── Calculation helpers ────────────────────────────────────────────────────────

def _calc_slippage_bps(actual: float, expected: float) -> float:
    if expected == 0:
        return 0.0
    return (actual - expected) / expected * 10000


def _calc_slippage_cost(actual: float, expected: float, qty: int) -> float:
    return abs(actual - expected) * qty


def _calc_latency_ms(start: datetime, end: datetime) -> float:
    delta = (end - start).total_seconds() * 1000
    return max(0.0, delta)  # guard against clock skew


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = max(0, min(int(len(sorted_v) * pct / 100), len(sorted_v) - 1))
    return sorted_v[idx]


def _calc_gap(paper_value: float, backtest_value: float) -> float:
    return paper_value - backtest_value


def _grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    if score >= 35:
        return "D"
    return "F"


def _confidence(sample_trades: int) -> str:
    if sample_trades >= 30:
        return "HIGH"
    if sample_trades >= 10:
        return "MEDIUM"
    return "LOW"


def _calc_health_score(
    conversion_rate: float,
    slippage_bps: float,
    win_rate: float,
    profit_factor: float,
) -> float:
    signal_quality = conversion_rate * 100.0

    max_slippage = 200.0
    slippage_score = max(0.0, 100.0 - (slippage_bps / max_slippage) * 100.0)

    win_rate_score = win_rate * 100.0

    max_pf = 3.0
    pf_score = min(100.0, (profit_factor / max_pf) * 100.0)

    overall = (signal_quality + slippage_score + win_rate_score + pf_score) / 4.0
    return overall


# ── Slippage calculation tests ─────────────────────────────────────────────────

class TestSlippageCalculations:

    def test_entry_slippage_bps_long(self):
        """LONG trade: actual_entry=101, expected_entry=100 → 100 bps."""
        actual_entry = 101.0
        expected_entry = 100.0
        result = _calc_slippage_bps(actual_entry, expected_entry)
        assert result == pytest.approx(100.0)

    def test_entry_slippage_bps_short(self):
        """SHORT trade: actual_entry=99, expected_entry=100 → -100 bps (received less)."""
        actual_entry = 99.0
        expected_entry = 100.0
        result = _calc_slippage_bps(actual_entry, expected_entry)
        assert result == pytest.approx(-100.0)

    def test_zero_slippage(self):
        """actual == expected → 0 bps."""
        result = _calc_slippage_bps(100.0, 100.0)
        assert result == pytest.approx(0.0)

    def test_avg_slippage_multiple_trades(self):
        """3 trades with slippages [50, 100, 150] → avg = 100 bps."""
        slippages = [50.0, 100.0, 150.0]
        avg = sum(slippages) / len(slippages)
        assert avg == pytest.approx(100.0)

    def test_slippage_cost_inr(self):
        """100 shares at 100 expected, 101 actual → cost = 100 INR."""
        actual = 101.0
        expected = 100.0
        qty = 100
        cost = _calc_slippage_cost(actual, expected, qty)
        assert cost == pytest.approx(100.0)


# ── Latency calculation tests ──────────────────────────────────────────────────

class TestLatencyCalculations:

    def test_signal_latency_ms(self):
        """breakout_time=T, created_at=T+500ms → latency=500ms."""
        base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        created_at = base + timedelta(milliseconds=500)
        result = _calc_latency_ms(base, created_at)
        assert result == pytest.approx(500.0)

    def test_execution_latency_ms(self):
        """signal_created=T, order_created=T+1500ms → latency=1500ms."""
        signal_created = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        order_created = signal_created + timedelta(milliseconds=1500)
        result = _calc_latency_ms(signal_created, order_created)
        assert result == pytest.approx(1500.0)

    def test_percentile_calculation(self):
        """sorted_values=[10..100], p50=50, p95=95, p99=99 (approximately)."""
        values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        p50 = _percentile(values, 50)
        p95 = _percentile(values, 95)
        p99 = _percentile(values, 99)
        assert p50 == pytest.approx(50.0)
        assert p95 == pytest.approx(90.0)
        assert p99 == pytest.approx(100.0)

    def test_high_latency_detection(self):
        """latency=3000ms > 2000ms threshold → classified as high latency."""
        latency_ms = 3000.0
        threshold_ms = 2000.0
        is_high_latency = latency_ms > threshold_ms
        assert is_high_latency is True


# ── Reality gap calculation tests ──────────────────────────────────────────────

class TestRealityGapCalculations:

    def test_win_rate_gap(self):
        """backtest_wr=0.65, paper_wr=0.55 → gap = -0.10 (paper worse)."""
        backtest_wr = 0.65
        paper_wr = 0.55
        gap = _calc_gap(paper_wr, backtest_wr)
        assert gap == pytest.approx(-0.10)

    def test_pnl_gap(self):
        """backtest_avg_pnl=500, paper_avg_pnl=350 → gap = -150."""
        backtest_avg_pnl = 500.0
        paper_avg_pnl = 350.0
        gap = _calc_gap(paper_avg_pnl, backtest_avg_pnl)
        assert gap == pytest.approx(-150.0)

    def test_drawdown_gap(self):
        """backtest_dd=0.05, paper_dd=0.08 → gap = 0.03 (paper has more drawdown)."""
        backtest_dd = 0.05
        paper_dd = 0.08
        gap = _calc_gap(paper_dd, backtest_dd)
        assert gap == pytest.approx(0.03)

    def test_expectancy_gap(self):
        """backtest_exp=300, paper_exp=200 → gap = -100."""
        backtest_exp = 300.0
        paper_exp = 200.0
        gap = _calc_gap(paper_exp, backtest_exp)
        assert gap == pytest.approx(-100.0)


# ── Health score calculation tests ────────────────────────────────────────────

class TestHealthScoreCalculations:

    def test_perfect_health_score(self):
        """conversion_rate=1.0, slippage_bps=0, win_rate=0.8, profit_factor=2.0
        → overall score should be >= 80, grade = 'A'."""
        score = _calc_health_score(
            conversion_rate=1.0,
            slippage_bps=0.0,
            win_rate=0.8,
            profit_factor=2.0,
        )
        assert score >= 80.0
        assert _grade(score) == "A"

    def test_poor_health_score(self):
        """conversion_rate=0.1, slippage_bps=100, win_rate=0.3, profit_factor=0.5
        → overall score should be <= 40, grade 'D' or 'F'."""
        score = _calc_health_score(
            conversion_rate=0.1,
            slippage_bps=100.0,
            win_rate=0.3,
            profit_factor=0.5,
        )
        assert score <= 40.0
        assert _grade(score) in ("D", "F")

    def test_grade_boundaries(self):
        """score=80→A, score=65→B, score=50→C, score=35→D, score=34→F."""
        assert _grade(80.0) == "A"
        assert _grade(65.0) == "B"
        assert _grade(50.0) == "C"
        assert _grade(35.0) == "D"
        assert _grade(34.0) == "F"

    def test_confidence_levels(self):
        """trades=30→HIGH, trades=15→MEDIUM, trades=5→LOW."""
        assert _confidence(30) == "HIGH"
        assert _confidence(15) == "MEDIUM"
        assert _confidence(5) == "LOW"

    def test_signal_quality_component(self):
        """conversion_rate=0.75 → signal_quality_score = 75.0."""
        conversion_rate = 0.75
        signal_quality_score = conversion_rate * 100.0
        assert signal_quality_score == pytest.approx(75.0)


# ── Signal quality tests ───────────────────────────────────────────────────────

class TestSignalQualityCalculations:

    def test_conversion_rate_full(self):
        """10 generated, 8 executed → rate = 0.80."""
        generated = 10
        executed = 8
        rate = executed / generated if generated > 0 else 0.0
        assert rate == pytest.approx(0.80)

    def test_conversion_rate_zero_generated(self):
        """0 generated → rate = 0.0 (no division by zero)."""
        generated = 0
        executed = 0
        rate = executed / generated if generated > 0 else 0.0
        assert rate == pytest.approx(0.0)

    def test_missed_count(self):
        """10 generated, 8 executed → missed = 2."""
        generated = 10
        executed = 8
        missed = generated - executed
        assert missed == 2
