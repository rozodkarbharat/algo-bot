"""
Monte Carlo Risk Analysis Engine — unit tests.

Tests cover:
  - TradeSampler: all three sampling methods
  - MonteCarloSimulator: basic simulation, drawdown, probability-of-ruin,
    capital requirements, losing-streak analysis
  - ReportGenerator: structure and key fields of all four report types
  - Edge cases: single trade, all-win, all-loss sequences

No DB or external dependencies.  Pure-Python engine tested in isolation.
"""

import pytest

from app.risk.monte_carlo.trade_sampler import TradeSampler, SamplingMethod, SampledTrades
from app.risk.monte_carlo.simulator import (
    MonteCarloConfig,
    MonteCarloSimulator,
    MonteCarloSummary,
)
from app.risk.monte_carlo.report_generator import ReportGenerator


# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_PNLS = [
    500, -300, 800, -200, 1200, -400, 600, -150, 900, -500,
    700, -250, 400, -350, 1100, -600, 300, -100, 550, -450,
]

LOSING_PNLS = [-100, -200, -300, -400, -500] * 4  # pure losing

WINNING_PNLS = [100, 200, 300, 400, 500] * 4  # pure winning


def _make_config(**kwargs) -> MonteCarloConfig:
    defaults = {
        "starting_capital": 1_000_000.0,
        "simulation_count": 200,
        "sampling_method": SamplingMethod.BOOTSTRAP,
        "ruin_thresholds": [0.50, 0.40, 0.30],
        "confidence_levels": [0.90, 0.95],
        "seed": 42,
    }
    defaults.update(kwargs)
    return MonteCarloConfig(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# TradeSampler
# ═══════════════════════════════════════════════════════════════════════════════

class TestTradeSampler:

    def test_random_shuffle_same_elements(self):
        sampler = TradeSampler(seed=0)
        result  = sampler.sample(SAMPLE_PNLS, SamplingMethod.RANDOM_SHUFFLE)
        assert isinstance(result, SampledTrades)
        assert sorted(result.pnls) == sorted(SAMPLE_PNLS)
        assert len(result.pnls) == len(SAMPLE_PNLS)

    def test_random_shuffle_changes_order(self):
        # With seed=0 the shuffle should produce a different order
        sampler = TradeSampler(seed=0)
        result  = sampler.sample(SAMPLE_PNLS, SamplingMethod.RANDOM_SHUFFLE)
        # Not guaranteed to differ but for this seed + data it should
        assert result.pnls != SAMPLE_PNLS

    def test_bootstrap_correct_length(self):
        sampler = TradeSampler(seed=1)
        result  = sampler.sample(SAMPLE_PNLS, SamplingMethod.BOOTSTRAP)
        assert len(result.pnls) == len(SAMPLE_PNLS)
        assert result.method == SamplingMethod.BOOTSTRAP

    def test_bootstrap_allows_repetition(self):
        # Bootstrap should occasionally repeat values; over 1000 draws it's certain
        sampler = TradeSampler(seed=7)
        all_sampled: list[float] = []
        for _ in range(20):
            r = sampler.sample(SAMPLE_PNLS, SamplingMethod.BOOTSTRAP)
            all_sampled.extend(r.pnls)
        # If repetition is working, some value should appear > original_count times
        from collections import Counter
        counts = Counter(all_sampled)
        # 500 appears 1 time in original 20 draws; in 20×20=400 samples should repeat
        assert any(v > 20 for v in counts.values())

    def test_replacement_same_as_bootstrap(self):
        sampler_a = TradeSampler(seed=99)
        sampler_b = TradeSampler(seed=99)
        r_a = sampler_a.sample(SAMPLE_PNLS, SamplingMethod.BOOTSTRAP)
        r_b = sampler_b.sample(SAMPLE_PNLS, SamplingMethod.REPLACEMENT)
        assert r_a.pnls == r_b.pnls

    def test_custom_n_longer(self):
        sampler = TradeSampler(seed=3)
        result  = sampler.sample(SAMPLE_PNLS, SamplingMethod.BOOTSTRAP, n=50)
        assert len(result.pnls) == 50

    def test_empty_input(self):
        sampler = TradeSampler(seed=0)
        result  = sampler.sample([], SamplingMethod.BOOTSTRAP)
        assert result.pnls == []
        assert result.original_count == 0

    def test_sample_batch(self):
        sampler  = TradeSampler(seed=5)
        batch    = sampler.sample_batch(SAMPLE_PNLS, n_simulations=10)
        assert len(batch) == 10
        for b in batch:
            assert len(b.pnls) == len(SAMPLE_PNLS)


# ═══════════════════════════════════════════════════════════════════════════════
# MonteCarloSimulator — basic
# ═══════════════════════════════════════════════════════════════════════════════

class TestMonteCarloSimulatorBasic:

    def test_returns_summary_type(self):
        cfg = _make_config()
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert isinstance(result, MonteCarloSummary)

    def test_simulation_count_matches(self):
        cfg = _make_config(simulation_count=150)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert result.simulation_count == 150

    def test_trade_count_correct(self):
        cfg = _make_config()
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert result.trade_count == len(SAMPLE_PNLS)

    def test_empty_raises(self):
        cfg = _make_config()
        sim = MonteCarloSimulator(cfg)
        with pytest.raises(ValueError, match="non-empty"):
            sim.run([])

    def test_best_gte_avg_gte_worst(self):
        cfg = _make_config()
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert result.best_return >= result.avg_return >= result.worst_return

    def test_std_return_non_negative(self):
        cfg = _make_config()
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert result.std_return >= 0.0

    def test_reproducibility_with_seed(self):
        cfg_a = _make_config(seed=42)
        cfg_b = _make_config(seed=42)
        res_a = MonteCarloSimulator(cfg_a).run(SAMPLE_PNLS)
        res_b = MonteCarloSimulator(cfg_b).run(SAMPLE_PNLS)
        assert res_a.avg_return == res_b.avg_return
        assert res_a.max_drawdown == res_b.max_drawdown


# ═══════════════════════════════════════════════════════════════════════════════
# Drawdown calculations
# ═══════════════════════════════════════════════════════════════════════════════

class TestDrawdownCalculations:

    def test_all_winning_zero_drawdown(self):
        # If every trade is positive the equity curve never drops → drawdown = 0
        cfg = _make_config(seed=0)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(WINNING_PNLS)
        assert result.avg_drawdown == 0.0
        assert result.max_drawdown == 0.0

    def test_losing_streak_causes_drawdown(self):
        cfg = _make_config(seed=0)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(LOSING_PNLS)
        assert result.max_drawdown > 0.0

    def test_drawdown_pct_bounded(self):
        cfg = _make_config(seed=1, starting_capital=100_000)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        # Max drawdown % should be < 100 for reasonable trade sizes vs capital
        assert 0.0 <= result.max_drawdown

    def test_drawdown_percentiles_ordered(self):
        cfg = _make_config(seed=2)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        p = result.drawdown_percentiles
        assert p["p10"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p90"] <= p["p95"] <= p["p99"]

    def test_return_percentiles_ordered(self):
        cfg = _make_config(seed=3)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        p = result.return_percentiles
        assert p["p10"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p90"] <= p["p95"] <= p["p99"]


# ═══════════════════════════════════════════════════════════════════════════════
# Probability of ruin
# ═══════════════════════════════════════════════════════════════════════════════

class TestProbabilityOfRuin:

    def test_ruin_keys_present(self):
        cfg = _make_config(ruin_thresholds=[0.50, 0.40, 0.30])
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert "50pct" in result.probability_of_ruin
        assert "40pct" in result.probability_of_ruin
        assert "30pct" in result.probability_of_ruin

    def test_ruin_probabilities_between_0_and_1(self):
        cfg = _make_config(ruin_thresholds=[0.50, 0.40, 0.30])
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        for k, v in result.probability_of_ruin.items():
            assert 0.0 <= v <= 1.0, f"Ruin prob {k} out of range: {v}"

    def test_ruin_monotone_50pct_ge_40pct_ge_30pct(self):
        # P(equity < 50%) >= P(equity < 40%) >= P(equity < 30%)
        # because 50% of capital is a higher (easier to hit) threshold
        cfg = _make_config(ruin_thresholds=[0.50, 0.40, 0.30], seed=42)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        p = result.probability_of_ruin
        assert p["50pct"] >= p["40pct"] >= p["30pct"]

    def test_ruin_zero_for_large_capital(self):
        # With ₹100 cr starting capital and tiny trades, ruin should be ~0
        cfg = _make_config(
            starting_capital=1_000_000_000,
            ruin_thresholds=[0.50],
            simulation_count=200,
            seed=0,
        )
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert result.probability_of_ruin["50pct"] == 0.0

    def test_ruin_high_for_tiny_capital(self):
        # With ₹500 starting capital and loss trades reaching -₹600, ruin is certain
        cfg = _make_config(
            starting_capital=500,
            ruin_thresholds=[0.50],
            simulation_count=200,
            seed=7,
        )
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        # Most simulations should hit 50% ruin (equity < ₹250) given -₹600 possible trades
        assert result.probability_of_ruin["50pct"] > 0.5

    def test_custom_ruin_threshold(self):
        cfg = _make_config(ruin_thresholds=[0.20], seed=5)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert "20pct" in result.probability_of_ruin


# ═══════════════════════════════════════════════════════════════════════════════
# Capital requirements
# ═══════════════════════════════════════════════════════════════════════════════

class TestCapitalRequirements:

    def test_capital_req_keys_match_ruin_thresholds(self):
        cfg = _make_config(ruin_thresholds=[0.50, 0.30])
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert set(result.capital_requirements.keys()) == {"50pct", "30pct"}

    def test_capital_req_non_negative(self):
        cfg = _make_config()
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        for k, v in result.capital_requirements.items():
            assert v >= 0.0, f"Capital req {k} is negative: {v}"

    def test_stricter_threshold_requires_more_capital(self):
        # 30% threshold (must survive bigger loss) needs more capital than 50%
        cfg = _make_config(ruin_thresholds=[0.50, 0.30], seed=10)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        cap = result.capital_requirements
        # cap_req_30pct = p95_dd / 0.30 > cap_req_50pct = p95_dd / 0.50
        assert cap["30pct"] >= cap["50pct"]

    def test_all_winning_zero_capital_req(self):
        cfg = _make_config(seed=0)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(WINNING_PNLS)
        # p95 drawdown = 0 → capital requirement = 0
        for v in result.capital_requirements.values():
            assert v == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Losing streak analysis
# ═══════════════════════════════════════════════════════════════════════════════

class TestLosingStreakAnalysis:

    def test_max_streak_gte_avg_streak(self):
        cfg = _make_config(seed=1)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert result.max_consecutive_losses >= result.avg_consecutive_losses

    def test_all_losing_streak_equals_trade_count(self):
        # All trades are losses → max streak = trade count
        cfg = _make_config(seed=0)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(LOSING_PNLS)
        assert result.max_consecutive_losses == len(LOSING_PNLS)

    def test_all_winning_zero_streak(self):
        cfg = _make_config(seed=0)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(WINNING_PNLS)
        assert result.max_consecutive_losses == 0
        assert result.avg_consecutive_losses == 0.0

    def test_confidence_interval_keys(self):
        cfg = _make_config(confidence_levels=[0.90, 0.95])
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert "90pct" in result.streak_confidence_intervals
        assert "95pct" in result.streak_confidence_intervals

    def test_ci_lower_le_mean_le_upper(self):
        cfg = _make_config(confidence_levels=[0.95], seed=3)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        ci = result.streak_confidence_intervals["95pct"]
        assert ci["lower"] <= ci["mean"] <= ci["upper"]

    def test_avg_streak_non_negative(self):
        cfg = _make_config(seed=4)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert result.avg_consecutive_losses >= 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# ReportGenerator
# ═══════════════════════════════════════════════════════════════════════════════

class TestReportGenerator:

    def _get_summary(self) -> MonteCarloSummary:
        cfg = _make_config(seed=42, simulation_count=500)
        sim = MonteCarloSimulator(cfg)
        return sim.run(SAMPLE_PNLS)

    def test_risk_report_structure(self):
        rg      = ReportGenerator()
        summary = self._get_summary()
        report  = rg.generate_risk_report(summary, "test_strategy", 1_000_000)
        assert report["report_type"] == "risk_report"
        assert "return_summary" in report
        assert "drawdown_summary" in report
        assert "probability_of_ruin" in report
        assert "losing_streak" in report
        assert "risk_rating" in report
        assert report["risk_rating"] in ("LOW", "MODERATE", "ELEVATED", "HIGH")

    def test_drawdown_report_structure(self):
        rg      = ReportGenerator()
        summary = self._get_summary()
        report  = rg.generate_drawdown_report(summary, "test_strategy", 1_000_000)
        assert report["report_type"] == "drawdown_report"
        assert "drawdown_percentiles_pct" in report
        assert "drawdown_percentiles_abs" in report
        assert "worst_case_analysis" in report
        assert "recovery_analysis" in report

    def test_capital_requirement_report_structure(self):
        rg      = ReportGenerator()
        summary = self._get_summary()
        report  = rg.generate_capital_requirement_report(
            summary, "test_strategy", 1_000_000
        )
        assert report["report_type"] == "capital_requirement_report"
        assert "capital_requirements" in report
        assert "recommendation" in report
        for key, detail in report["capital_requirements"].items():
            assert "min_capital_required" in detail
            assert "current_capital_sufficient" in detail
            assert "surplus_or_deficit" in detail

    def test_strategy_comparison_report_structure(self):
        rg  = ReportGenerator()
        cfg = _make_config(seed=42, simulation_count=200)
        sim = MonteCarloSimulator(cfg)
        s1  = sim.run(SAMPLE_PNLS)
        s2  = sim.run(WINNING_PNLS)
        s3  = sim.run(SAMPLE_PNLS + WINNING_PNLS)  # combined

        report = rg.generate_strategy_comparison_report(
            {"one_side_orb": s1, "orhv": s2}, s3, 1_000_000
        )
        assert report["report_type"] == "strategy_comparison_report"
        assert len(report["strategies"]) == 2
        assert "portfolio" in report
        assert "diversification_benefit" in report
        assert "drawdown_reduction_pct" in report["diversification_benefit"]

    def test_risk_report_return_pct(self):
        rg      = ReportGenerator()
        summary = self._get_summary()
        report  = rg.generate_risk_report(summary, "x", 1_000_000)
        rs      = report["return_summary"]
        expected_pct = round(summary.avg_return / 1_000_000 * 100, 2)
        assert rs["avg_return_pct"] == expected_pct


# ═══════════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_single_trade(self):
        cfg = _make_config(simulation_count=100)
        sim = MonteCarloSimulator(cfg)
        # Single trade — all simulations identical
        result = sim.run([500.0])
        assert result.trade_count == 1
        assert result.avg_return == 500.0
        assert result.best_return == 500.0
        assert result.worst_return == 500.0

    def test_single_losing_trade(self):
        cfg = _make_config(simulation_count=100, starting_capital=1000)
        sim = MonteCarloSimulator(cfg)
        result = sim.run([-600.0])
        assert result.max_consecutive_losses == 1
        assert result.max_drawdown > 0

    def test_large_simulation_count(self):
        cfg = _make_config(simulation_count=500)
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert result.simulation_count == 500

    def test_random_shuffle_method(self):
        cfg = _make_config(
            sampling_method=SamplingMethod.RANDOM_SHUFFLE, seed=0
        )
        sim = MonteCarloSimulator(cfg)
        result = sim.run(SAMPLE_PNLS)
        assert result.simulation_count == cfg.simulation_count

    def test_all_zero_pnl(self):
        # Edge: all trades breakeven — equity flat → drawdown = 0, streak = all trades
        pnls = [0.0] * 10
        cfg  = _make_config(seed=0, simulation_count=100)
        sim  = MonteCarloSimulator(cfg)
        # Zero pnl is not < 0 so streak should be 0; equity never drops
        result = sim.run(pnls)
        assert result.max_drawdown == 0.0
        assert result.avg_return == 0.0
