"""
BacktestMetrics — aggregate performance metrics for a completed backtest run.

One document per BacktestRun. Contains all statistical metrics computed by
the MetricsEngine from the raw BacktestTrade records.

Stored separately from BacktestRun to keep the run document lean and allow
independent re-computation of metrics without re-running the simulation.
"""

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BacktestMetrics(Document):
    """
    Complete performance statistics for one backtest run.

    Collection: backtest_metrics
    Unique constraint: run_id
    """

    run_id: str = Field(..., description="Foreign key to BacktestRun.run_id")

    # ── Trade counts ──────────────────────────────────────────────────────────
    total_trades: int = Field(default=0, description="Total executed trades (entry taken)")
    winning_trades: int = Field(default=0, description="Trades with net positive P&L")
    losing_trades: int = Field(default=0, description="Trades with net negative P&L")
    no_entry_days: int = Field(
        default=0,
        description="Candidate days where price never broke the ORB (no trade taken)",
    )
    total_candidate_days: int = Field(
        default=0,
        description="Total days where the strategy identified a setup (incl. no-entry)",
    )

    # ── Rate metrics ──────────────────────────────────────────────────────────
    win_rate: float = Field(
        default=0.0,
        description="Winning trades / total_trades (0.0–1.0)",
    )
    sl_hit_rate: float = Field(
        default=0.0,
        description="SL-hit trades / total_trades (0.0–1.0)",
    )
    breakout_success_rate: float = Field(
        default=0.0,
        description="Total_trades / total_candidate_days — how often breakout actually occurred",
    )

    # ── P&L metrics ───────────────────────────────────────────────────────────
    total_pnl: float = Field(default=0.0, description="Sum of net P&L across all trades (₹)")
    avg_pnl_per_trade: float = Field(
        default=0.0,
        description="Mean P&L per executed trade",
    )
    avg_win: float = Field(default=0.0, description="Average P&L of winning trades")
    avg_loss: float = Field(default=0.0, description="Average P&L of losing trades (negative)")
    max_win: float = Field(default=0.0, description="Largest single winning trade P&L")
    max_loss: float = Field(default=0.0, description="Largest single loss (most negative)")

    # ── Risk metrics ──────────────────────────────────────────────────────────
    max_drawdown: float = Field(
        default=0.0,
        description="Maximum peak-to-trough equity decline (₹)",
    )
    max_drawdown_percent: float = Field(
        default=0.0,
        description="Max drawdown as % of peak equity",
    )
    profit_factor: float = Field(
        default=0.0,
        description="Gross profit / gross loss (> 1.0 = profitable)",
    )
    expectancy: float = Field(
        default=0.0,
        description="Expected P&L per trade: (win_rate × avg_win) − (loss_rate × |avg_loss|)",
    )
    sharpe_ratio: Optional[float] = Field(
        default=None,
        description="Annualised Sharpe ratio of daily returns (√252 normalised)",
    )
    avg_risk_reward: Optional[float] = Field(
        default=None,
        description="Average achieved R-multiple across all executed trades",
    )

    # ── Consecutive stats ─────────────────────────────────────────────────────
    max_consecutive_wins: int = Field(default=0)
    max_consecutive_losses: int = Field(default=0)

    # ── Breakdowns (serialised as JSON-compatible dicts) ─────────────────────
    per_symbol_metrics: dict = Field(
        default_factory=dict,
        description=(
            "Per-symbol breakdown: symbol → {total, wins, losses, pnl, win_rate, "
            "avg_pnl, best_trade, worst_trade}"
        ),
    )
    daily_pnl: dict = Field(
        default_factory=dict,
        description="Daily net P&L keyed by 'YYYY-MM-DD' (IST date)",
    )
    monthly_pnl: dict = Field(
        default_factory=dict,
        description="Monthly net P&L keyed by 'YYYY-MM'",
    )

    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "backtest_metrics"
        indexes = [
            IndexModel([("run_id", ASCENDING)], unique=True, name="run_id_unique"),
        ]
