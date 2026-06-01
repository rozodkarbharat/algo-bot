"""
Paper trade ledger — one document per completed paper trade.

A PaperTrade is written when a PaperPosition transitions to CLOSED. The
trade ledger is the audit-log: positions can be reset/archived but a trade
record persists for performance reporting, equity curves, and post-mortems.

The trade row is broker-agnostic and stores the full lifecycle: entry,
exit, slippage, brokerage, net pnl and exit reason. The accompanying
position is referenced via `position_id` for cross-lookup.
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional
from uuid import uuid4

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.models.paper_position import PaperTradeSide


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_trade_id() -> str:
    return uuid4().hex


class PaperExitReason(StrEnum):
    """Why a paper position was closed."""

    SL_HIT = "SL_HIT"            # stop loss triggered
    EOD_EXIT = "EOD_EXIT"         # forced exit at end of session
    MANUAL_CLOSE = "MANUAL_CLOSE" # operator closed via API
    RISK_HALT = "RISK_HALT"       # risk manager halted trading


class PaperTrade(Document):
    """
    Completed paper trade — append-only audit ledger.

    Collection: paper_trades
    """

    trade_id: str = Field(default_factory=_new_trade_id)
    position_id: str = Field(..., description="Originating PaperPosition.position_id")
    signal_id: Optional[str] = Field(default=None, description="Originating LiveSignal id")

    symbol: str
    trading_date: datetime = Field(..., description="Trading date (UTC midnight)")
    trade_side: PaperTradeSide

    quantity: int
    entry_price: float = Field(..., description="Filled entry price (incl. slippage)")
    exit_price: float = Field(..., description="Filled exit price (incl. slippage)")
    stop_loss: float

    exit_reason: PaperExitReason

    # Cost components — all in ₹.
    slippage: float = Field(..., description="Total slippage cost (₹) across entry + exit")
    brokerage: float = Field(..., description="Total brokerage cost (₹) across entry + exit")

    pnl: float = Field(..., description="Net P&L after slippage and brokerage")
    pnl_percent: float = Field(..., description="Net P&L as % of capital deployed")

    opened_at: datetime
    closed_at: datetime

    # ── Multi-strategy fields ─────────────────────────────────────────────────
    strategy_id: str = Field(
        default="one_side_orb",
        description="Strategy that generated the originating signal",
    )
    strategy_name: str = Field(
        default="One-Side ORB",
        description="Human-readable strategy name",
    )
    strategy_version: str = Field(
        default="1.0.0",
        description="Strategy version at trade entry time",
    )

    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "paper_trades"
        indexes = [
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("trading_date", ASCENDING)]),
            IndexModel([("opened_at", DESCENDING)]),
            IndexModel([("closed_at", DESCENDING)]),
            IndexModel([("strategy_id", ASCENDING)]),
            IndexModel([("trade_id", ASCENDING)], unique=True, name="trade_id_unique"),
            IndexModel([("position_id", ASCENDING)], name="position_id_idx"),
            IndexModel([("exit_reason", ASCENDING)]),
        ]
