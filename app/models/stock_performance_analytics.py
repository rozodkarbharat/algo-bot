"""
StockPerformanceAnalytics — aggregated per-symbol performance across a research run.

One document per symbol, upserted by ResearchService after each research run.
Provides a live "stock leaderboard" view without re-running analytics each time.
"""

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StockPerformanceAnalytics(Document):
    """
    Aggregated One-Side ORB performance for a single NSE symbol.

    Collection: stock_performance_analytics
    Unique constraint: symbol

    Updated by StockAnalyticsEngine after each research run completes.
    Suitable for building "which stocks are worth trading?" rankings.
    """

    symbol: str = Field(..., description="NSE ticker symbol")

    # ── Trade volume ──────────────────────────────────────────────────────────
    total_trades: int = Field(default=0)
    winning_trades: int = Field(default=0)
    losing_trades: int = Field(default=0)

    # ── Rate metrics ──────────────────────────────────────────────────────────
    win_rate: float = Field(default=0.0, description="Fraction of executed trades that were profitable")
    sl_hit_rate: float = Field(default=0.0, description="Fraction of executed trades that hit stop-loss")
    breakout_success_rate: float = Field(
        default=0.0,
        description="Fraction of candidate days that produced an entry (vs NO_BREAKOUT)",
    )

    # ── P&L metrics ───────────────────────────────────────────────────────────
    total_pnl: float = Field(default=0.0)
    avg_pnl: float = Field(default=0.0, description="Average P&L per executed trade (₹)")
    max_win: float = Field(default=0.0, description="Best single trade P&L (₹)")
    max_loss: float = Field(default=0.0, description="Worst single trade P&L (₹)")

    # ── Risk metrics ──────────────────────────────────────────────────────────
    expectancy: float = Field(default=0.0, description="Expected value per trade: win_rate×avg_win − loss_rate×|avg_loss|")
    profit_factor: float = Field(default=0.0)
    max_drawdown: float = Field(default=0.0, description="Max sequential drawdown (₹) across this symbol's trades")

    # ── Time edge ─────────────────────────────────────────────────────────────
    best_breakout_time_range: Optional[str] = Field(
        default=None,
        description="IST time bucket (e.g. '09:30–10:00') with highest win rate for this symbol",
    )

    # ── Breakout quality ──────────────────────────────────────────────────────
    avg_orb_range_pct: float = Field(
        default=0.0,
        description="Average ORB range % for trades taken on this symbol",
    )
    avg_move_after_breakout_pct: float = Field(
        default=0.0,
        description="Average % move from ORB boundary to exit price",
    )

    # ── Source tracking ───────────────────────────────────────────────────────
    last_run_id: Optional[str] = Field(default=None, description="Most recent ResearchRun.run_id that updated this record")
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "stock_performance_analytics"
        indexes = [
            IndexModel([("symbol", ASCENDING)], unique=True, name="spa_symbol_unique"),
            IndexModel([("win_rate", DESCENDING)]),
            IndexModel([("total_pnl", DESCENDING)]),
            IndexModel([("expectancy", DESCENDING)]),
        ]
