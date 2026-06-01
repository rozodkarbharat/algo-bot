"""
Unit tests for the ReportGenerator.

Tests verify:
  - Report is generated even when all inputs are None (graceful degradation)
  - Each section is populated when the corresponding analytics result is provided
  - Recommendations are generated and are non-empty strings
  - to_dict() produces a JSON-serialisable output
  - Executive summary extracts the correct best parameter values

All tests are pure Python — no database, no I/O.

Run with:
    pytest tests/test_report_generator.py -v
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.models.backtest_trade import ExitReason, TradeSide
from app.research.failure_analytics import (
    FailureAnalyticsEngine,
    FailureAnalyticsResult,
)
from app.research.market_condition_analytics import (
    MarketConditionAnalyticsEngine,
    MarketConditionResult,
)
from app.research.parameter_optimizer import (
    OptimizationPoint,
    ParameterGrid,
    ResearchConfig,
    SweepResult,
)
from app.research.report_generator import ReportGenerator, ResearchReport
from app.research.stock_analytics import (
    StockAnalyticsEngine,
    StockAnalyticsResult,
    SymbolAnalytics,
)
from app.research.time_analytics import TimeAnalyticsEngine, TimeAnalyticsResult
from app.strategy.backtest_engine import BacktestConfig
from app.strategy.metrics_engine import MetricsResult
from app.strategy.trade_simulator import SimulatedTrade


# ── Helpers ───────────────────────────────────────────────────────────────────

def _minimal_metrics(
    total_pnl: float = 5000.0,
    win_rate: float = 0.60,
    total_trades: int = 20,
) -> MetricsResult:
    return MetricsResult(
        run_id="test-run",
        total_trades=total_trades,
        winning_trades=int(total_trades * win_rate),
        losing_trades=total_trades - int(total_trades * win_rate),
        win_rate=win_rate,
        sl_hit_rate=0.3,
        total_pnl=total_pnl,
        avg_pnl_per_trade=total_pnl / total_trades,
        expectancy=total_pnl / total_trades,
        profit_factor=1.5,
        max_drawdown=1000.0,
        breakout_success_rate=0.4,
    )


def _minimal_sweep_result() -> SweepResult:
    base_cfg = BacktestConfig(
        from_date=date(2024, 1, 2),
        to_date=date(2024, 3, 29),
        probability_threshold=0.70,
    )
    sweep = SweepResult(run_id="test-run", total_configs_run=3)
    sweep.points = [
        OptimizationPoint(
            parameter_name="probability_threshold",
            parameter_value="0.60",
            config=base_cfg,
            metrics=_minimal_metrics(total_pnl=3000.0, win_rate=0.55),
        ),
        OptimizationPoint(
            parameter_name="probability_threshold",
            parameter_value="0.70",
            config=base_cfg,
            metrics=_minimal_metrics(total_pnl=5000.0, win_rate=0.62),
        ),
        OptimizationPoint(
            parameter_name="probability_threshold",
            parameter_value="0.80",
            config=base_cfg,
            metrics=_minimal_metrics(total_pnl=4000.0, win_rate=0.68),
        ),
    ]
    return sweep


def _minimal_stock_result() -> StockAnalyticsResult:
    top = SymbolAnalytics(
        symbol="RELIANCE", total_trades=15, winning_trades=9,
        win_rate=0.60, sl_hit_rate=0.20, total_pnl=3000.0,
        avg_pnl=200.0, expectancy=200.0, profit_factor=2.0,
        tradability_score=0.12,
    )
    bad = SymbolAnalytics(
        symbol="TOXIC", total_trades=10, winning_trades=3,
        win_rate=0.30, sl_hit_rate=0.70, total_pnl=-1500.0,
        avg_pnl=-150.0, expectancy=-150.0, profit_factor=0.4,
        tradability_score=0.0, max_loss=-500.0, max_win=300.0,
    )
    result = StockAnalyticsResult(
        symbol_analytics=[top, bad],
        top_performers=[top],
        worst_performers=[bad],
        high_sl_risk=[bad],
        metadata={"total_symbols": 2, "qualified_symbols": 2},
    )
    return result


# ── ReportGenerator tests ─────────────────────────────────────────────────────

class TestReportGenerator:
    def test_generate_with_all_none_inputs(self):
        gen = ReportGenerator()
        report = gen.generate(run_id="test-id")
        assert isinstance(report, ResearchReport)
        assert report.run_id == "test-id"
        # Sections built from analytics engines are empty when inputs are None
        assert report.parameter_sensitivity == {}
        assert report.stock_rankings == {}
        assert report.time_edge == {}
        assert report.market_conditions == {}
        assert report.failure_diagnostics == {}
        assert isinstance(report.recommendations, list)
        # Executive summary always has the strategy label
        assert "strategy" in report.executive_summary

    def test_generate_with_sweep_only(self):
        gen = ReportGenerator()
        sweep = _minimal_sweep_result()
        report = gen.generate(run_id="test-id", sweep_result=sweep)
        assert "probability_threshold" in report.parameter_sensitivity
        pt = report.parameter_sensitivity["probability_threshold"]
        assert "ranking" in pt
        assert len(pt["ranking"]) == 3

    def test_parameter_sensitivity_sorted_by_pnl_descending(self):
        gen = ReportGenerator()
        sweep = _minimal_sweep_result()
        report = gen.generate(run_id="test-id", sweep_result=sweep)
        pt = report.parameter_sensitivity["probability_threshold"]
        pnls = [r["total_pnl"] for r in pt["ranking"]]
        assert pnls == sorted(pnls, reverse=True)

    def test_best_parameter_value_in_executive_summary(self):
        gen = ReportGenerator()
        sweep = _minimal_sweep_result()
        report = gen.generate(run_id="test-id", sweep_result=sweep)
        # Best is probability_threshold=0.70 with pnl=5000
        best = report.executive_summary.get("best_probability_threshold", {})
        assert best.get("value") == "0.70"
        assert best.get("total_pnl") == pytest.approx(5000.0)

    def test_stock_rankings_section_present(self):
        gen = ReportGenerator()
        stock = _minimal_stock_result()
        report = gen.generate(run_id="test-id", stock_result=stock)
        assert "top_performers" in report.stock_rankings
        assert "worst_performers" in report.stock_rankings
        assert "high_sl_risk" in report.stock_rankings
        top = report.stock_rankings["top_performers"]
        assert len(top) == 1
        assert top[0]["symbol"] == "RELIANCE"

    def test_recommendations_are_non_empty_strings(self):
        gen = ReportGenerator()
        sweep = _minimal_sweep_result()
        stock = _minimal_stock_result()
        report = gen.generate(run_id="test-id", sweep_result=sweep, stock_result=stock)
        for rec in report.recommendations:
            assert isinstance(rec, str)
            assert len(rec) > 0

    def test_recommendations_include_high_sl_risk_warning(self):
        gen = ReportGenerator()
        # Build stock result with a high-SL symbol
        bad_sym = SymbolAnalytics(
            symbol="BADSTOCK", total_trades=10, winning_trades=2,
            win_rate=0.20, sl_hit_rate=0.80, total_pnl=-2000.0,
            avg_pnl=-200.0, expectancy=-200.0, profit_factor=0.2,
            tradability_score=0.0,
        )
        stock = StockAnalyticsResult(
            symbol_analytics=[bad_sym],
            top_performers=[],
            worst_performers=[bad_sym],
            high_sl_risk=[bad_sym],
            metadata={},
        )
        report = gen.generate(run_id="x", stock_result=stock)
        combined = " ".join(report.recommendations)
        assert "BADSTOCK" in combined or "excluding" in combined.lower()

    def test_to_dict_is_json_serialisable(self):
        gen = ReportGenerator()
        sweep = _minimal_sweep_result()
        stock = _minimal_stock_result()
        report = gen.generate(run_id="json-test", sweep_result=sweep, stock_result=stock)
        d = report.to_dict()
        # Must not raise
        serialised = json.dumps(d)
        parsed = json.loads(serialised)
        assert parsed["run_id"] == "json-test"

    def test_metadata_lists_generated_sections(self):
        gen = ReportGenerator()
        sweep = _minimal_sweep_result()
        report = gen.generate(run_id="meta-test", sweep_result=sweep)
        sections = report.metadata.get("sections_generated", [])
        assert "parameter_sensitivity" in sections
        # Other sections not passed → not in list
        assert "stock_rankings" not in sections

    def test_fallback_recommendation_when_no_inputs(self):
        gen = ReportGenerator()
        report = gen.generate(run_id="empty")
        assert len(report.recommendations) >= 1
        assert "insufficient" in report.recommendations[0].lower()

    def test_failure_diagnostics_section_populated(self):
        gen = ReportGenerator()
        # Build a minimal FailureAnalyticsResult via the engine
        from app.strategy.trade_simulator import SimulatedTrade
        from datetime import datetime

        def _sl_trade() -> SimulatedTrade:
            return SimulatedTrade(
                symbol="X",
                trade_side=TradeSide.LONG,
                breakout_side="UP",
                orb_high=100.0,
                orb_low=98.0,
                probability_score=0.70,
                entry_time=datetime(2024, 1, 3, 4, 15, tzinfo=timezone.utc),
                entry_price=100.0,
                stop_loss=98.0,
                exit_time=datetime(2024, 1, 3, 5, 0, tzinfo=timezone.utc),
                exit_price=98.0,
                exit_reason=ExitReason.SL_HIT,
                quantity=10,
                capital_used=1000.0,
                pnl=-20.0,
                pnl_percent=-2.0,
                risk_reward=-1.0,
            )

        trades = [_sl_trade() for _ in range(5)]
        failure_result = FailureAnalyticsEngine().analyse(trades)
        report = gen.generate(run_id="failure-test", failure_result=failure_result)

        fd = report.failure_diagnostics
        assert "overall" in fd
        assert fd["overall"]["total_sl_hits"] == 5
        assert "fake_breakouts" in fd
        assert "high_risk_symbols" in fd
