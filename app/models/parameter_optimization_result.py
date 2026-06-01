"""
ParameterOptimizationResult — one document per (run_id, parameter_name, parameter_value).

Each document captures the full performance of the strategy when a single
parameter is set to a specific value while all other parameters stay at
their base defaults. This enables univariate sensitivity analysis.
"""

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ParameterOptimizationResult(Document):
    """
    Performance metrics for one parameter value in an optimization sweep.

    Collection: parameter_optimization_results

    Index strategy:
      - (run_id, parameter_name) for fetching all values of one parameter in a run
      - parameter_name alone for cross-run comparisons
      - win_rate / total_pnl for server-side sorting in ranked queries
    """

    run_id: str = Field(..., description="Foreign key to ResearchRun.run_id")

    # ── What was varied ───────────────────────────────────────────────────────
    parameter_name: str = Field(
        ...,
        description=(
            "Which parameter was swept. One of: 'probability_threshold', "
            "'max_orb_range_pct', 'max_entry_time_ist', 'sl_buffer_pct'"
        ),
    )
    parameter_value: str = Field(
        ...,
        description="Parameter value cast to string for uniform DB storage",
    )

    # Full config snapshot so results are self-contained and reproducible
    configuration: dict = Field(default_factory=dict)

    # ── Core trade counts ─────────────────────────────────────────────────────
    total_trades: int = Field(default=0)
    winning_trades: int = Field(default=0)
    losing_trades: int = Field(default=0)
    no_entry_days: int = Field(default=0)
    total_candidate_days: int = Field(default=0)

    # ── Rate metrics ──────────────────────────────────────────────────────────
    win_rate: float = Field(default=0.0)
    sl_hit_rate: float = Field(default=0.0)
    breakout_success_rate: float = Field(default=0.0)

    # ── P&L ──────────────────────────────────────────────────────────────────
    total_pnl: float = Field(default=0.0)
    avg_pnl_per_trade: float = Field(default=0.0)
    avg_win: float = Field(default=0.0)
    avg_loss: float = Field(default=0.0)

    # ── Risk ──────────────────────────────────────────────────────────────────
    expectancy: float = Field(default=0.0)
    profit_factor: float = Field(default=0.0)
    max_drawdown: float = Field(default=0.0)
    max_drawdown_percent: float = Field(default=0.0)
    sharpe_ratio: Optional[float] = Field(default=None)

    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "parameter_optimization_results"
        indexes = [
            IndexModel([("run_id", ASCENDING)]),
            IndexModel([("parameter_name", ASCENDING)]),
            IndexModel(
                [("run_id", ASCENDING), ("parameter_name", ASCENDING)],
                name="run_param_compound",
            ),
            IndexModel([("win_rate", DESCENDING)]),
            IndexModel([("total_pnl", DESCENDING)]),
        ]
