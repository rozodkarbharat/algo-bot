"""
Unit tests for the walk-forward subsystem.

Tests verify:
  - WalkForwardWindowGenerator: window generation, boundaries, error cases
  - WalkForwardAggregator: PnL aggregation, win rate, empty/failed handling
  - RobustnessAnalyzer: stability scoring, edge cases, score range

All tests are pure Python — no database, no I/O.

Run with:
    pytest tests/test_walk_forward.py -v
"""

from dataclasses import dataclass, field
from datetime import date

import pytest

from app.research.walk_forward.window_generator import (
    WalkForwardConfig,
    WalkForwardWindow,
    WalkForwardWindowGenerator,
)
from app.research.walk_forward.aggregator import AggregatedResult, WalkForwardAggregator
from app.research.walk_forward.robustness_analyzer import RobustnessAnalyzer, RobustnessResult
from app.research.walk_forward.engine import SegmentResult


# ── Mock helpers ──────────────────────────────────────────────────────────────

@dataclass
class _MockTrade:
    """Minimal stand-in for SimulatedTrade — only .pnl is needed by the aggregator."""
    pnl: float


@dataclass
class _MockMetrics:
    """
    Minimal stand-in for MetricsResult.

    Mirrors the fields that the aggregator and robustness analyzer read:
    total_pnl, win_rate, sharpe_ratio, max_drawdown, profit_factor.
    """
    run_id: str = "test"
    total_trades: int = 10
    winning_trades: int = 6
    losing_trades: int = 4
    total_pnl: float = 5000.0
    win_rate: float = 0.6
    sharpe_ratio: float = 1.2
    max_drawdown: float = -2000.0
    profit_factor: float = 1.5


def _mock_window(segment_number: int = 1) -> WalkForwardWindow:
    return WalkForwardWindow(
        segment_number=segment_number,
        training_start=date(2023, 1, 1),
        training_end=date(2023, 12, 31),
        testing_start=date(2024, 1, 1),
        testing_end=date(2024, 3, 31),
    )


def _make_segment(
    segment_number: int = 1,
    pnl: float = 5000.0,
    win_rate: float = 0.6,
    sharpe: float = 1.2,
    max_drawdown: float = -2000.0,
    profit_factor: float = 1.5,
    params: dict | None = None,
    error: str | None = None,
    trades: list | None = None,
) -> SegmentResult:
    """Build a SegmentResult backed by lightweight mock objects."""
    if params is None:
        params = {
            "probability_threshold": 0.70,
            "max_orb_range_pct": 1.0,
            "max_entry_time_ist": "11:30",
            "sl_buffer_pct": 0.0,
        }
    metrics = _MockMetrics(
        total_pnl=pnl,
        win_rate=win_rate,
        sharpe_ratio=sharpe,
        max_drawdown=max_drawdown,
        profit_factor=profit_factor,
    )
    return SegmentResult(
        window=_mock_window(segment_number),
        selected_parameters=params,
        optimization_sharpe=sharpe,
        oos_metrics=metrics,  # type: ignore[arg-type]
        oos_trades=trades if trades is not None else [],
        error=error,
    )


def _cfg(
    from_date: date,
    to_date: date,
    training_months: int = 12,
    testing_months: int = 3,
    step_months: int = 3,
) -> WalkForwardConfig:
    return WalkForwardConfig(
        from_date=from_date,
        to_date=to_date,
        training_months=training_months,
        testing_months=testing_months,
        step_months=step_months,
    )


# ── WalkForwardWindowGenerator ────────────────────────────────────────────────

class TestWindowGeneration:
    def test_basic_window_generation(self):
        # 12m train + 3m test, 24m range starting Jan 2022
        # window 1: train Jan–Dec 2022, test Jan–Mar 2023
        # window 2: train Apr 2022–Mar 2023, test Apr–Jun 2023
        # window 3: train Jul 2022–Jun 2023, test Jul–Sep 2023
        # window 4: train Oct 2022–Sep 2023 → test Oct–Dec 2023 → fits exactly
        cfg = _cfg(
            from_date=date(2022, 1, 1),
            to_date=date(2023, 12, 31),
            training_months=12,
            testing_months=3,
            step_months=3,
        )
        windows = WalkForwardWindowGenerator(cfg).generate()
        # 24m range with 12m train + 3m test stepping by 3m → expect ≥ 3 windows
        assert len(windows) >= 3

    def test_single_window(self):
        # 12m train + 3m test, range is exactly 15m → fits one window
        cfg = _cfg(
            from_date=date(2023, 1, 1),
            to_date=date(2024, 3, 31),
            training_months=12,
            testing_months=3,
            step_months=3,
        )
        windows = WalkForwardWindowGenerator(cfg).generate()
        assert len(windows) == 1

    def test_window_count_is_one_based_and_sequential(self):
        cfg = _cfg(
            from_date=date(2022, 1, 1),
            to_date=date(2024, 12, 31),
            training_months=12,
            testing_months=3,
            step_months=3,
        )
        windows = WalkForwardWindowGenerator(cfg).generate()
        assert len(windows) >= 2
        for i, w in enumerate(windows, start=1):
            assert w.segment_number == i

    def test_window_boundaries_no_overlap(self):
        cfg = _cfg(
            from_date=date(2022, 1, 1),
            to_date=date(2024, 12, 31),
            training_months=12,
            testing_months=3,
            step_months=3,
        )
        windows = WalkForwardWindowGenerator(cfg).generate()
        for w in windows:
            assert w.training_end < w.testing_start, (
                f"Window #{w.segment_number}: training_end {w.training_end} "
                f"must be strictly before testing_start {w.testing_start}"
            )
            # testing_start must be exactly training_end + 1 day
            from datetime import timedelta
            assert w.testing_start == w.training_end + timedelta(days=1)

    def test_minimum_range_error(self):
        # Range of 6m is too short for 12m train + 3m test
        cfg = _cfg(
            from_date=date(2023, 1, 1),
            to_date=date(2023, 6, 30),
            training_months=12,
            testing_months=3,
            step_months=3,
        )
        with pytest.raises(ValueError, match="too short"):
            WalkForwardWindowGenerator(cfg).generate()

    @pytest.mark.parametrize("step_months,expected_fewer", [
        (3, False),   # baseline
        (6, True),    # larger step → fewer windows
    ])
    def test_step_size_affects_window_count(self, step_months, expected_fewer):
        base_cfg = _cfg(
            from_date=date(2020, 1, 1),
            to_date=date(2024, 12, 31),
            training_months=12,
            testing_months=3,
            step_months=3,
        )
        base_count = len(WalkForwardWindowGenerator(base_cfg).generate())

        cfg = _cfg(
            from_date=date(2020, 1, 1),
            to_date=date(2024, 12, 31),
            training_months=12,
            testing_months=3,
            step_months=step_months,
        )
        count = len(WalkForwardWindowGenerator(cfg).generate())

        if expected_fewer:
            assert count < base_count
        else:
            assert count == base_count

    def test_month_end_handling_jan31(self):
        # Jan 31 + 1 month should land on Feb 28/29, not raise an error
        cfg = _cfg(
            from_date=date(2023, 1, 31),
            to_date=date(2024, 6, 30),
            training_months=12,
            testing_months=3,
            step_months=3,
        )
        windows = WalkForwardWindowGenerator(cfg).generate()
        assert len(windows) >= 1
        first = windows[0]
        # training_end should be Feb 28 or 29 of the following year, not a crash
        assert first.training_end.month in (1, 2)  # clamped to valid Feb date


# ── WalkForwardAggregator ─────────────────────────────────────────────────────

class TestWalkForwardAggregator:
    def test_aggregate_basic_total_pnl(self):
        segments = [
            _make_segment(1, pnl=3000.0, trades=[_MockTrade(1000), _MockTrade(2000)]),
            _make_segment(2, pnl=5000.0, trades=[_MockTrade(5000)]),
            _make_segment(3, pnl=-1000.0, trades=[_MockTrade(-1000)]),
        ]
        agg = WalkForwardAggregator().aggregate(segments)  # type: ignore[arg-type]

        expected_pnl = 1000 + 2000 + 5000 + (-1000)
        assert agg.total_pnl == pytest.approx(expected_pnl)
        assert agg.total_segments == 3
        assert agg.completed_segments == 3
        assert agg.failed_segments == 0

    def test_aggregate_win_rate(self):
        # 2 out of 3 segments have positive PnL → walk_forward_win_rate = 2/3
        segments = [
            _make_segment(1, pnl=4000.0),
            _make_segment(2, pnl=2000.0),
            _make_segment(3, pnl=-500.0),
        ]
        agg = WalkForwardAggregator().aggregate(segments)  # type: ignore[arg-type]
        assert agg.walk_forward_win_rate == pytest.approx(2 / 3, rel=1e-3)

    def test_aggregate_empty(self):
        agg = WalkForwardAggregator().aggregate([])
        assert agg.total_segments == 0
        assert agg.total_pnl == 0.0
        assert agg.walk_forward_win_rate == 0.0
        assert agg.completed_segments == 0
        assert agg.failed_segments == 0

    def test_aggregate_skips_failed_segments(self):
        segments = [
            _make_segment(1, pnl=3000.0, trades=[_MockTrade(3000)]),
            _make_segment(2, pnl=2000.0, trades=[_MockTrade(2000)], error="engine crash"),
            _make_segment(3, pnl=1000.0, trades=[_MockTrade(1000)]),
        ]
        agg = WalkForwardAggregator().aggregate(segments)  # type: ignore[arg-type]

        # Only segments 1 and 3 are completed; segment 2 is excluded from metrics
        assert agg.total_segments == 3
        assert agg.completed_segments == 2
        assert agg.failed_segments == 1
        assert agg.total_pnl == pytest.approx(3000.0 + 1000.0)

    def test_aggregate_all_failed(self):
        segments = [
            _make_segment(1, error="timeout"),
            _make_segment(2, error="no data"),
        ]
        agg = WalkForwardAggregator().aggregate(segments)  # type: ignore[arg-type]
        assert agg.failed_segments == 2
        assert agg.completed_segments == 0
        assert agg.total_pnl == 0.0

    def test_aggregate_best_and_worst_segment(self):
        segments = [
            _make_segment(1, pnl=500.0),
            _make_segment(2, pnl=8000.0),
            _make_segment(3, pnl=-200.0),
        ]
        agg = WalkForwardAggregator().aggregate(segments)  # type: ignore[arg-type]
        assert agg.best_segment == 2
        assert agg.worst_segment == 3


# ── RobustnessAnalyzer ────────────────────────────────────────────────────────

class TestRobustnessAnalyzer:
    def test_high_robustness_identical_params_consistent_returns(self):
        # All windows select the exact same parameters and have similar PnLs
        stable_params = {
            "probability_threshold": 0.70,
            "max_orb_range_pct": 1.0,
            "max_entry_time_ist": "11:30",
            "sl_buffer_pct": 0.0,
        }
        segments = [
            _make_segment(i, pnl=5000.0, params=stable_params.copy())
            for i in range(1, 6)
        ]
        result = RobustnessAnalyzer().analyze(segments)  # type: ignore[arg-type]
        assert result.robustness_score > 70.0

    def test_low_robustness_wildly_different_params_volatile_returns(self):
        # Each window uses a completely different threshold and PnLs swing wildly
        pnls = [10000.0, -8000.0, 12000.0, -9000.0, 11000.0]
        thresholds = [0.50, 0.95, 0.55, 0.90, 0.60]
        segments = [
            _make_segment(
                i + 1,
                pnl=pnls[i],
                params={
                    "probability_threshold": thresholds[i],
                    "max_orb_range_pct": 1.0 + i * 0.5,
                    "max_entry_time_ist": "11:30",
                    "sl_buffer_pct": 0.0,
                },
            )
            for i in range(5)
        ]
        result = RobustnessAnalyzer().analyze(segments)  # type: ignore[arg-type]
        assert result.robustness_score < 50.0

    def test_single_segment_defaults_to_neutral(self):
        # Fewer than 2 completed segments → all dimension scores default to 50.0
        segments = [_make_segment(1, pnl=3000.0)]
        result = RobustnessAnalyzer().analyze(segments)  # type: ignore[arg-type]
        assert result.robustness_score == pytest.approx(50.0)
        assert result.parameter_stability_score == pytest.approx(50.0)
        assert result.performance_consistency_score == pytest.approx(50.0)
        assert result.regime_sensitivity_score == pytest.approx(50.0)

    def test_single_failed_segment_defaults_to_neutral(self):
        # The one segment has an error → completed < 2 → neutral defaults apply
        segments = [_make_segment(1, error="backtest failed")]
        result = RobustnessAnalyzer().analyze(segments)  # type: ignore[arg-type]
        assert result.robustness_score == pytest.approx(50.0)

    @pytest.mark.parametrize("pnls,params_list", [
        # All identical → high robustness
        (
            [5000.0, 5000.0, 5000.0],
            [{"probability_threshold": 0.70, "max_orb_range_pct": 1.0, "max_entry_time_ist": "11:30", "sl_buffer_pct": 0.0}] * 3,
        ),
        # Mild variation → moderate robustness
        (
            [3000.0, 4000.0, 3500.0],
            [
                {"probability_threshold": 0.70, "max_orb_range_pct": 1.0, "max_entry_time_ist": "11:30", "sl_buffer_pct": 0.0},
                {"probability_threshold": 0.72, "max_orb_range_pct": 1.0, "max_entry_time_ist": "11:30", "sl_buffer_pct": 0.0},
                {"probability_threshold": 0.71, "max_orb_range_pct": 1.0, "max_entry_time_ist": "11:30", "sl_buffer_pct": 0.0},
            ],
        ),
    ])
    def test_robustness_score_always_in_range(self, pnls, params_list):
        segments = [
            _make_segment(i + 1, pnl=pnls[i], params=params_list[i])
            for i in range(len(pnls))
        ]
        result = RobustnessAnalyzer().analyze(segments)  # type: ignore[arg-type]
        assert 0.0 <= result.robustness_score <= 100.0
        assert 0.0 <= result.parameter_stability_score <= 100.0
        assert 0.0 <= result.performance_consistency_score <= 100.0
        assert 0.0 <= result.regime_sensitivity_score <= 100.0

    def test_robustness_result_to_dict_is_complete(self):
        segments = [
            _make_segment(1, pnl=3000.0),
            _make_segment(2, pnl=4000.0),
        ]
        result = RobustnessAnalyzer().analyze(segments)  # type: ignore[arg-type]
        d = result.to_dict()
        expected_keys = {
            "robustness_score",
            "parameter_stability_score",
            "performance_consistency_score",
            "regime_sensitivity_score",
            "parameter_variance",
            "most_stable_parameters",
            "least_stable_parameters",
            "return_coefficient_of_variation",
            "profitable_segments_pct",
            "best_window_pnl",
            "worst_window_pnl",
            "pnl_range_pct",
        }
        assert expected_keys <= set(d.keys())

    def test_best_and_worst_window_pnl_populated(self):
        pnls = [1000.0, 8000.0, -500.0, 4000.0]
        params = {"probability_threshold": 0.70, "max_orb_range_pct": 1.0, "max_entry_time_ist": "11:30", "sl_buffer_pct": 0.0}
        segments = [_make_segment(i + 1, pnl=pnls[i], params=params.copy()) for i in range(4)]
        result = RobustnessAnalyzer().analyze(segments)  # type: ignore[arg-type]
        assert result.best_window_pnl == pytest.approx(8000.0)
        assert result.worst_window_pnl == pytest.approx(-500.0)
