"""
BacktestTrade — one document per simulated trade in a backtest run.

Each document represents a single trade attempt for (symbol, trading_date).
Includes both executed trades (with entry/exit prices) and candidate days
where price never broke out (exit_reason=NO_BREAKOUT).

Linked to BacktestRun via run_id.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TradeSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(str, Enum):
    SL_HIT = "SL_HIT"          # stop loss was triggered
    EOD_EXIT = "EOD_EXIT"       # held to 3:15 PM EOD exit
    NO_BREAKOUT = "NO_BREAKOUT" # candidate day but price never broke the ORB


class BacktestTrade(Document):
    """
    A single simulated trade record within a backtest run.

    Collection: backtest_trades
    Key indexes: run_id, (run_id, symbol), (run_id, trading_date)
    """

    run_id: str = Field(..., description="Foreign key to BacktestRun.run_id")
    symbol: str = Field(..., description="NSE ticker symbol")
    trading_date: datetime = Field(..., description="Trade date (UTC midnight)")

    # ── Multi-strategy fields ─────────────────────────────────────────────────
    strategy_id: str = Field(default="one_side_orb", description="Strategy that generated this trade")
    strategy_name: str = Field(default="One-Side ORB", description="Human-readable strategy name")

    # Direction of the trade — derived from yesterday's OSD direction
    trade_side: TradeSide = Field(..., description="LONG or SHORT")
    breakout_side: str = Field(..., description="UP or DOWN (yesterday's OSD direction)")

    # Opening range boundaries (first 15-min candle of the trade day)
    orb_high: float = Field(..., description="First candle high (ORB high)")
    orb_low: float = Field(..., description="First candle low (ORB low)")

    # Probability score at entry decision time
    probability_score: float = Field(
        default=0.0,
        description="Continuation probability at time of trade decision",
    )

    # Entry — None when exit_reason=NO_BREAKOUT
    entry_time: Optional[datetime] = Field(default=None, description="Entry candle time (UTC)")
    entry_price: Optional[float] = Field(default=None, description="Actual entry price (incl. slippage)")

    # Risk parameters
    stop_loss: float = Field(..., description="Stop loss price (ORB boundary ± buffer)")

    # Exit — None when exit_reason=NO_BREAKOUT
    exit_time: Optional[datetime] = Field(default=None, description="Exit candle time (UTC)")
    exit_price: Optional[float] = Field(default=None, description="Actual exit price (incl. slippage)")
    exit_reason: ExitReason = Field(..., description="Reason the trade was closed")

    # P&L — all zero for NO_BREAKOUT
    quantity: int = Field(default=0, description="Number of shares traded")
    capital_used: float = Field(default=0.0, description="Capital deployed (qty × entry_price)")
    pnl: float = Field(default=0.0, description="Net P&L after brokerage")
    pnl_percent: float = Field(default=0.0, description="P&L as % of capital_used")
    risk_reward: Optional[float] = Field(
        default=None,
        description="Achieved R-multiple: (exit_price - entry) / (entry - stop_loss)",
    )

    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "backtest_trades"
        indexes = [
            IndexModel([("run_id", ASCENDING)]),
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("trading_date", ASCENDING)]),
            IndexModel([("run_id", ASCENDING), ("symbol", ASCENDING)]),
            IndexModel([("run_id", ASCENDING), ("trading_date", ASCENDING)]),
            IndexModel(
                [("run_id", ASCENDING), ("symbol", ASCENDING), ("trading_date", ASCENDING)],
                name="run_symbol_date",
            ),
        ]
