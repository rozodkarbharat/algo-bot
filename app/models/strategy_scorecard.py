import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ScorecardDataSource(str, Enum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"
    COMBINED = "COMBINED"


class StrategyScorecard(Document):
    scorecard_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    catalog_id: str = Field(...)
    strategy_id: str = Field(...)
    computed_at: datetime = Field(default_factory=_utcnow)
    data_source: ScorecardDataSource = Field(default=ScorecardDataSource.BACKTEST)
    backtest_run_id: Optional[str] = Field(default=None)
    period_from: Optional[datetime] = Field(default=None)
    period_to: Optional[datetime] = Field(default=None)

    # Core metrics
    win_rate: Optional[float] = Field(default=None, description="Win rate as a fraction (0.0 to 1.0)")
    expectancy: Optional[float] = Field(default=None, description="Expected P&L per trade in INR")
    max_drawdown: Optional[float] = Field(default=None, description="Max drawdown as a positive fraction (e.g. 0.15 = 15%)")
    sharpe_ratio: Optional[float] = Field(default=None)
    profit_factor: Optional[float] = Field(default=None)
    total_trades: Optional[int] = Field(default=None)
    total_pnl: Optional[float] = Field(default=None)

    # Research quality metrics
    walk_forward_score: Optional[float] = Field(default=None, description="Walk-forward robustness score (0.0 to 1.0)")
    monte_carlo_score: Optional[float] = Field(default=None, description="Monte Carlo probability of profitability (0.0 to 1.0)")

    # Overall computed score
    overall_score: Optional[float] = Field(default=None, description="Weighted composite score (0.0 to 100.0)")
    score_breakdown: dict = Field(default_factory=dict, description="Component weights and values used to derive overall_score")

    # Extra
    notes: str = Field(default="")
    metadata: dict = Field(default_factory=dict)

    class Settings:
        name = "strategy_scorecards"
        indexes = [
            IndexModel([("scorecard_id", ASCENDING)], unique=True, name="scorecard_id_unique"),
            IndexModel(
                [("strategy_id", ASCENDING), ("computed_at", DESCENDING)],
                name="strategy_computed_at_compound",
            ),
            IndexModel([("catalog_id", ASCENDING)], name="catalog_id_asc"),
            IndexModel([("overall_score", DESCENDING)], name="overall_score_desc"),
        ]

    @classmethod
    def compute_overall_score(
        cls,
        win_rate: Optional[float],
        expectancy: Optional[float],
        max_drawdown: Optional[float],
        sharpe_ratio: Optional[float],
        walk_forward_score: Optional[float],
        monte_carlo_score: Optional[float],
    ) -> tuple[float, dict]:
        """Compute a weighted composite score and return (overall_score, score_breakdown).

        Weights:
            win_rate        20%  — higher win rate is better
            expectancy      15%  — INR 500 expectancy maps to 100 score
            max_drawdown    20%  — lower drawdown is better
            sharpe_ratio    20%  — Sharpe 3.0 maps to 100 score
            walk_forward    15%  — direct 0-1 scale from WalkForward engine
            monte_carlo     10%  — probability of profitability from MC engine

        Returns:
            (overall_score, score_breakdown) where overall_score is in [0.0, 100.0].
        """
        _wr = win_rate if win_rate is not None else 0.0
        _ex = expectancy if expectancy is not None else 0.0
        _dd = max_drawdown if max_drawdown is not None else 0.0
        _sr = sharpe_ratio if sharpe_ratio is not None else 0.0

        win_rate_score = min(_wr * 100, 100.0) * 0.20
        expectancy_score = min(max(_ex, 0.0) / 500.0 * 100.0, 100.0) * 0.15
        drawdown_score = max(0.0, (1.0 - _dd) * 100.0) * 0.20
        sharpe_score = min(max(_sr, 0.0) / 3.0 * 100.0, 100.0) * 0.20
        wf_score_component = (walk_forward_score or 0.0) * 100.0 * 0.15
        mc_score_component = (monte_carlo_score or 0.0) * 100.0 * 0.10

        overall = (
            win_rate_score
            + expectancy_score
            + drawdown_score
            + sharpe_score
            + wf_score_component
            + mc_score_component
        )

        breakdown = {
            "win_rate": {
                "input": win_rate,
                "component_score": round(win_rate_score, 4),
                "weight": 0.20,
            },
            "expectancy": {
                "input": expectancy,
                "component_score": round(expectancy_score, 4),
                "weight": 0.15,
            },
            "max_drawdown": {
                "input": max_drawdown,
                "component_score": round(drawdown_score, 4),
                "weight": 0.20,
            },
            "sharpe_ratio": {
                "input": sharpe_ratio,
                "component_score": round(sharpe_score, 4),
                "weight": 0.20,
            },
            "walk_forward_score": {
                "input": walk_forward_score,
                "component_score": round(wf_score_component, 4),
                "weight": 0.15,
            },
            "monte_carlo_score": {
                "input": monte_carlo_score,
                "component_score": round(mc_score_component, 4),
                "weight": 0.10,
            },
            "overall_score": round(overall, 4),
        }

        return round(overall, 4), breakdown
