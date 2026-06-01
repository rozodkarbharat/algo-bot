"""
Unit tests for the signal ranking engine.

All pure logic — no MongoDB, no async.
"""

from __future__ import annotations

import pytest

from app.portfolio.signal_ranker import (
    DEFAULT_WEIGHTS,
    RankResult,
    SignalRankInput,
    SignalRanker,
    _validate_weights,
    rank_signals,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _inp(
    symbol: str = "RELIANCE",
    strategy_id: str = "one_side_orb",
    probability_score: float | None = 0.75,
    historical_win_rate: float | None = 0.65,
    historical_expectancy: float | None = 3_000.0,
    historical_max_drawdown: float | None = -20_000.0,
    continuation_probability: float | None = 0.70,
) -> SignalRankInput:
    return SignalRankInput(
        symbol=symbol,
        strategy_id=strategy_id,
        probability_score=probability_score,
        historical_win_rate=historical_win_rate,
        historical_expectancy=historical_expectancy,
        historical_max_drawdown=historical_max_drawdown,
        continuation_probability=continuation_probability,
    )


# ── Weight validation ─────────────────────────────────────────────────────────

def test_default_weights_sum_to_one():
    total = sum(DEFAULT_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9


def test_custom_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1.0"):
        SignalRanker(weights={"win_rate": 0.5, "expectancy": 0.3})


def test_validate_weights_passes_when_correct():
    _validate_weights({"win_rate": 0.4, "expectancy": 0.6})  # should not raise


# ── Score range ───────────────────────────────────────────────────────────────

def test_score_is_between_zero_and_one():
    ranker = SignalRanker()
    result = ranker.rank(_inp())
    assert 0.0 <= result.ranking_score <= 1.0


def test_perfect_inputs_score_near_one():
    ranker = SignalRanker()
    result = ranker.rank(
        _inp(
            probability_score=1.0,
            historical_win_rate=1.0,
            historical_expectancy=5_000.0,   # ceiling
            historical_max_drawdown=0.0,
            continuation_probability=1.0,
        )
    )
    assert result.ranking_score > 0.9


def test_zero_inputs_score_near_zero():
    ranker = SignalRanker()
    result = ranker.rank(
        _inp(
            probability_score=0.0,
            historical_win_rate=0.0,
            historical_expectancy=-5_000.0,
            historical_max_drawdown=-100_000.0,
            continuation_probability=0.0,
        )
    )
    assert result.ranking_score < 0.3


def test_all_none_inputs_score_near_half():
    """Unknown inputs default to neutral (0.5) → total ≈ 0.5."""
    ranker = SignalRanker()
    result = ranker.rank(
        _inp(
            probability_score=None,
            historical_win_rate=None,
            historical_expectancy=None,
            historical_max_drawdown=None,
            continuation_probability=None,
        )
    )
    assert abs(result.ranking_score - 0.5) < 0.01


# ── Factor components ─────────────────────────────────────────────────────────

def test_rank_result_has_all_components():
    ranker = SignalRanker()
    result = ranker.rank(_inp())
    for factor in ("win_rate", "expectancy", "probability_score", "stock_reliability", "drawdown_penalty"):
        assert factor in result.components
        assert factor in result.weighted_components


def test_weighted_components_sum_to_ranking_score():
    ranker = SignalRanker()
    result = ranker.rank(_inp())
    reconstructed = sum(result.weighted_components.values())
    assert abs(reconstructed - result.ranking_score) < 1e-5


def test_higher_probability_produces_higher_score():
    ranker = SignalRanker()
    low = ranker.rank(_inp(probability_score=0.5))
    high = ranker.rank(_inp(probability_score=0.9))
    assert high.ranking_score > low.ranking_score


def test_negative_expectancy_lowers_score():
    ranker = SignalRanker()
    good = ranker.rank(_inp(historical_expectancy=2_000.0))
    bad = ranker.rank(_inp(historical_expectancy=-2_000.0))
    assert good.ranking_score > bad.ranking_score


def test_larger_drawdown_lowers_score():
    ranker = SignalRanker()
    shallow = ranker.rank(_inp(historical_max_drawdown=-5_000.0))
    deep = ranker.rank(_inp(historical_max_drawdown=-80_000.0))
    assert shallow.ranking_score > deep.ranking_score


# ── Normalisation edge cases ──────────────────────────────────────────────────

def test_win_rate_clamped_above_one():
    ranker = SignalRanker()
    result = ranker.rank(_inp(historical_win_rate=1.5))
    assert result.components["win_rate"] == 1.0


def test_win_rate_clamped_below_zero():
    ranker = SignalRanker()
    result = ranker.rank(_inp(historical_win_rate=-0.1))
    assert result.components["win_rate"] == 0.0


def test_zero_drawdown_gives_max_drawdown_factor():
    ranker = SignalRanker()
    result = ranker.rank(_inp(historical_max_drawdown=0.0))
    assert result.components["drawdown_penalty"] == 1.0


# ── rank_signals helper ───────────────────────────────────────────────────────

def test_rank_signals_returns_sorted_descending():
    inputs = [
        _inp(symbol="A", probability_score=0.5),
        _inp(symbol="B", probability_score=0.9),
        _inp(symbol="C", probability_score=0.3),
    ]
    ranked = rank_signals(inputs)
    scores = [r.ranking_score for _, r in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_signals_preserves_all_items():
    inputs = [_inp(symbol=s) for s in ["A", "B", "C", "D"]]
    ranked = rank_signals(inputs)
    assert len(ranked) == 4


def test_rank_signals_empty_input():
    assert rank_signals([]) == []
