"""
Unit tests for the ParameterOptimizer.

Tests verify:
  - ParameterGrid builds the correct number of sweep entries
  - _build_sweep_configs() produces the right parameter variants
  - run_sweep() calls the engine for each config and collects results
  - Failed sweep points increment failed_configs without crashing
  - SweepResult is correctly populated

All tests are pure Python — no database, no I/O.

Run with:
    pytest tests/test_parameter_optimizer.py -v
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from app.models.backtest_trade import ExitReason, TradeSide
from app.research.parameter_optimizer import (
    OptimizationPoint,
    ParameterGrid,
    ParameterOptimizer,
    ResearchConfig,
    SweepResult,
)
from app.strategy.backtest_engine import BacktestConfig
from app.strategy.metrics_engine import MetricsResult
from app.strategy.trade_simulator import SimulatedTrade


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _minimal_config() -> ResearchConfig:
    return ResearchConfig(
        from_date=date(2024, 1, 2),
        to_date=date(2024, 3, 29),
        grid=ParameterGrid(
            probability_thresholds=[0.60, 0.70, 0.80],
            orb_range_filters=[0.5, 1.0],
            entry_cutoff_times=["10:30", "11:30"],
            sl_buffers=[0.0, 0.05],
        ),
    )


def _make_trade(pnl: float = 100.0) -> SimulatedTrade:
    return SimulatedTrade(
        symbol="RELIANCE",
        trade_side=TradeSide.LONG,
        breakout_side="UP",
        orb_high=2600.0,
        orb_low=2560.0,
        probability_score=0.72,
        entry_time=datetime(2024, 1, 3, 4, 15, tzinfo=timezone.utc),
        entry_price=2602.0,
        stop_loss=2560.0,
        exit_time=datetime(2024, 1, 3, 9, 45, tzinfo=timezone.utc),
        exit_price=2640.0,
        exit_reason=ExitReason.EOD_EXIT,
        quantity=38,
        capital_used=98_876.0,
        pnl=pnl,
        pnl_percent=pnl / 98_876.0 * 100,
        risk_reward=0.9,
    )


# ── ParameterGrid tests ───────────────────────────────────────────────────────

class TestParameterGrid:
    def test_default_grid_is_non_empty(self):
        grid = ParameterGrid()
        assert len(grid.probability_thresholds) > 0
        assert len(grid.orb_range_filters) > 0
        assert len(grid.entry_cutoff_times) > 0
        assert len(grid.sl_buffers) > 0

    def test_custom_grid_respected(self):
        grid = ParameterGrid(
            probability_thresholds=[0.65],
            orb_range_filters=[0.5, 1.0, 1.5],
            entry_cutoff_times=["10:00"],
            sl_buffers=[0.0],
        )
        assert grid.probability_thresholds == [0.65]
        assert len(grid.orb_range_filters) == 3


# ── _build_sweep_configs tests ────────────────────────────────────────────────

class TestBuildSweepConfigs:
    def test_total_configs_equals_sum_of_grid_sizes(self):
        cfg = _minimal_config()
        optimizer = ParameterOptimizer(cfg)
        configs = optimizer._build_sweep_configs()
        expected = (
            len(cfg.grid.probability_thresholds)
            + len(cfg.grid.orb_range_filters)
            + len(cfg.grid.entry_cutoff_times)
            + len(cfg.grid.sl_buffers)
        )
        assert len(configs) == expected

    def test_each_config_varies_only_one_parameter(self):
        cfg = _minimal_config()
        optimizer = ParameterOptimizer(cfg)
        configs = optimizer._build_sweep_configs()

        for param_name, param_value, backtest_cfg in configs:
            assert isinstance(backtest_cfg, BacktestConfig)
            assert param_name in (
                "probability_threshold",
                "max_orb_range_pct",
                "max_entry_time_ist",
                "sl_buffer_pct",
            )

    def test_probability_threshold_values_are_correct(self):
        cfg = _minimal_config()
        optimizer = ParameterOptimizer(cfg)
        configs = optimizer._build_sweep_configs()

        prob_entries = [
            (name, val, bcfg)
            for name, val, bcfg in configs
            if name == "probability_threshold"
        ]
        assert len(prob_entries) == len(cfg.grid.probability_thresholds)
        actual_values = [float(val) for _, val, _ in prob_entries]
        assert actual_values == cfg.grid.probability_thresholds

    def test_non_swept_parameters_stay_at_base(self):
        cfg = _minimal_config()
        cfg.base_max_orb_range_pct = 0.75
        optimizer = ParameterOptimizer(cfg)
        configs = optimizer._build_sweep_configs()

        # Probability sweep entries should keep base_max_orb_range_pct
        prob_entries = [
            bcfg for name, _, bcfg in configs if name == "probability_threshold"
        ]
        for bcfg in prob_entries:
            assert bcfg.max_orb_range_pct == 0.75

    def test_date_range_propagated_to_all_configs(self):
        cfg = _minimal_config()
        optimizer = ParameterOptimizer(cfg)
        configs = optimizer._build_sweep_configs()
        for _, _, bcfg in configs:
            assert bcfg.from_date == cfg.from_date
            assert bcfg.to_date == cfg.to_date


# ── run_sweep tests ───────────────────────────────────────────────────────────

class TestRunSweep:
    def _mock_engine_result(self, trades: list):
        from app.strategy.backtest_engine import BacktestEngineResult
        result = BacktestEngineResult(
            trades=trades,
            total_candidate_days=len(trades),
            total_no_data_days=0,
            symbols_processed=["RELIANCE"],
            trading_days_processed=10,
        )
        return result

    def test_sweep_returns_one_point_per_config(self):
        cfg = _minimal_config()
        optimizer = ParameterOptimizer(cfg)
        total_expected = (
            len(cfg.grid.probability_thresholds)
            + len(cfg.grid.orb_range_filters)
            + len(cfg.grid.entry_cutoff_times)
            + len(cfg.grid.sl_buffers)
        )

        trade = _make_trade(pnl=150.0)
        fake_engine_result = self._mock_engine_result([trade])

        with patch(
            "app.research.parameter_optimizer.BacktestEngine"
        ) as MockEngine:
            instance = MockEngine.return_value
            instance.run.return_value = fake_engine_result

            result = optimizer.run_sweep(
                run_id="test-run-id",
                symbols=["RELIANCE"],
                prob_scores={"RELIANCE": 0.72},
                osd_history={},
                candle_history={},
            )

        assert result.total_configs_run == total_expected
        assert len(result.points) == total_expected
        assert result.failed_configs == 0

    def test_failed_engine_increments_failed_count(self):
        cfg = _minimal_config()
        optimizer = ParameterOptimizer(cfg)

        with patch(
            "app.research.parameter_optimizer.BacktestEngine"
        ) as MockEngine:
            MockEngine.return_value.run.side_effect = RuntimeError("simulated engine crash")

            result = optimizer.run_sweep(
                run_id="test-run-id",
                symbols=["RELIANCE"],
                prob_scores={"RELIANCE": 0.72},
                osd_history={},
                candle_history={},
            )

        assert result.failed_configs > 0
        assert len(result.points) == 0

    def test_optimization_point_has_correct_parameter_name(self):
        cfg = ResearchConfig(
            from_date=date(2024, 1, 2),
            to_date=date(2024, 3, 29),
            grid=ParameterGrid(
                probability_thresholds=[0.70],
                orb_range_filters=[],
                entry_cutoff_times=[],
                sl_buffers=[],
            ),
        )
        optimizer = ParameterOptimizer(cfg)

        trade = _make_trade(pnl=200.0)
        from app.strategy.backtest_engine import BacktestEngineResult
        fake_result = BacktestEngineResult(
            trades=[trade],
            total_candidate_days=1,
            total_no_data_days=0,
            symbols_processed=["RELIANCE"],
            trading_days_processed=1,
        )
        with patch("app.research.parameter_optimizer.BacktestEngine") as MockEngine:
            MockEngine.return_value.run.return_value = fake_result
            result = optimizer.run_sweep(
                run_id="x", symbols=["RELIANCE"],
                prob_scores={"RELIANCE": 0.72},
                osd_history={}, candle_history={},
            )

        assert len(result.points) == 1
        assert result.points[0].parameter_name == "probability_threshold"
        assert result.points[0].parameter_value == "0.7"


# ── ResearchConfig serialisation ──────────────────────────────────────────────

class TestResearchConfigSerialization:
    def test_to_dict_is_json_serialisable(self):
        import json
        cfg = _minimal_config()
        d = cfg.to_dict()
        # Should not raise
        json.dumps(d)

    def test_to_dict_contains_required_keys(self):
        cfg = _minimal_config()
        d = cfg.to_dict()
        assert "from_date" in d
        assert "to_date" in d
        assert "base_probability_threshold" in d
        assert "grid_probability_thresholds" in d
