"""
Paper position document — one open or closed simulated position.

A position is created when the Paper Execution Engine fills a paper order
from a live signal. While open, the Position Manager updates `current_price`
and `unrealized_pnl` on every closed candle. On exit (SL hit, EOD, manual
close), the position transitions to CLOSED, `realized_pnl` is finalised and
a sibling PaperTrade document is written for the trade-history ledger.

Persistence contract:
  - `position_id` is an application-generated UUID4 hex — also used to link
    the PaperTrade ledger entry.
  - Unique constraint on (symbol, trading_date, status="OPEN") is enforced at
    the service layer (one open position per stock per day). The DB-level
    invariant `(symbol, trading_date)` for closed positions is intentionally
    NOT unique so reset/archival flows can keep history.
  - `trading_date` is stored as UTC midnight (matches LiveSignal convention).
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional
from uuid import uuid4

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_position_id() -> str:
    return uuid4().hex


class PaperTradeSide(StrEnum):
    """Direction of a simulated trade."""

    LONG = "LONG"
    SHORT = "SHORT"


class PaperPositionStatus(StrEnum):
    """Lifecycle of a paper position."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class PaperPosition(Document):
    """
    Open or closed simulated position.

    Collection: paper_positions
    """

    position_id: str = Field(default_factory=_new_position_id)

    symbol: str = Field(..., description="NSE ticker symbol")
    trading_date: datetime = Field(..., description="Trading date (UTC midnight)")

    trade_side: PaperTradeSide = Field(..., description="LONG or SHORT")

    quantity: int = Field(..., description="Number of shares held (always positive)")
    entry_price: float = Field(..., description="Filled entry price (incl. slippage)")
    current_price: float = Field(..., description="Most recent mark price")
    stop_loss: float = Field(..., description="Configured stop-loss level")

    unrealized_pnl: float = Field(
        default=0.0, description="Mark-to-market P&L while OPEN, frozen when CLOSED"
    )
    realized_pnl: float = Field(
        default=0.0, description="Net P&L (incl. brokerage) — populated on close"
    )

    status: PaperPositionStatus = Field(default=PaperPositionStatus.OPEN)

    # Provenance — link back to the live signal that created this position.
    signal_id: Optional[str] = Field(default=None, description="Source LiveSignal id")

    # Multi-strategy identity
    strategy_id: str = Field(default="one_side_orb", description="Strategy that generated the signal")
    strategy_name: str = Field(default="One-Side ORB", description="Human-readable strategy name")

    opened_at: datetime = Field(default_factory=_utcnow)
    closed_at: Optional[datetime] = Field(default=None)

    metadata: dict = Field(default_factory=dict)

    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "paper_positions"
        indexes = [
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("trading_date", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("position_id", ASCENDING)], unique=True, name="position_id_unique"),
            IndexModel(
                [("symbol", ASCENDING), ("trading_date", ASCENDING), ("status", ASCENDING)],
                name="symbol_date_status",
            ),
            IndexModel([("opened_at", DESCENDING)]),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()
