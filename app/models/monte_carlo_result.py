"""
MonteCarloResult — per-strategy (or combined) aggregated simulation output.

One MonteCarloRun produces multiple MonteCarloResult documents:
  - One per individual strategy_id analysed.
  - One with strategy_id="portfolio" for the combined multi-strategy view.

Collection: monte_carlo_results
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MonteCarloResult(Document):
    result_id:   str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id:      str = Field(...)
    # "portfolio" = combined; otherwise the strategy_id (e.g. "one_side_orb")
    strategy_id: str = Field(...)

    # ── Core return metrics ────────────────────────────────────────────────────
    avg_return:    float = Field(default=0.0)
    median_return: float = Field(default=0.0)
    best_return:   float = Field(default=0.0)
    worst_return:  float = Field(default=0.0)
    std_return:    float = Field(default=0.0)

    # ── Drawdown metrics (% of starting capital) ───────────────────────────────
    avg_drawdown: float = Field(default=0.0)
    max_drawdown: float = Field(default=0.0)

    # ── Probability of ruin ────────────────────────────────────────────────────
    # dict keyed "50pct", "40pct", "30pct" etc.
    probability_of_ruin: dict = Field(default_factory=dict)

    # ── Losing streak metrics ──────────────────────────────────────────────────
    avg_consecutive_losses: float = Field(default=0.0)
    max_consecutive_losses: int   = Field(default=0)

    # ── Extended metrics (stored as nested dicts) ──────────────────────────────
    return_percentiles:          dict = Field(default_factory=dict)
    drawdown_percentiles:        dict = Field(default_factory=dict)
    streak_confidence_intervals: dict = Field(default_factory=dict)
    capital_requirements:        dict = Field(default_factory=dict)

    trade_count:      int   = Field(default=0)
    simulation_count: int   = Field(default=0)
    starting_capital: float = Field(default=0.0)

    created_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "monte_carlo_results"
        indexes = [
            IndexModel([("result_id", ASCENDING)], unique=True, name="mc_result_id_unique"),
            IndexModel([("run_id", ASCENDING)]),
            IndexModel(
                [("run_id", ASCENDING), ("strategy_id", ASCENDING)],
                unique=True,
                name="mc_run_strategy_unique",
            ),
        ]
