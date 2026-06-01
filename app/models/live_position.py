"""
Live position document — one open or closed real-money position.

Mirrors `PaperPosition` in structure, but every row represents a real
position established by a broker fill. While open, the live position
manager updates `current_price` and `unrealized_pnl` on each closed
candle. On exit (SL hit, EOD, manual close, reconciliation discrepancy)
the position transitions to CLOSED and `realized_pnl` is finalised.

Persistence contract:
  - `position_id` is an application-generated UUID4 hex.
  - `entry_order_id` and `exit_order_id` link the position to its
    originating LiveOrder rows for full traceability.
  - One OPEN position per (symbol, trading_date) is enforced at the
    service layer (the live risk manager rejects duplicates upstream).
  - `trading_date` is stored as UTC midnight.
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional
from uuid import uuid4

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.models.live_order import LiveTradeSide


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_position_id() -> str:
    return uuid4().hex


class LivePositionStatus(StrEnum):
    """Lifecycle of a real-money position."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class LiveExitReason(StrEnum):
    """Why a live position was closed."""

    SL_HIT = "SL_HIT"                # stop loss triggered
    EOD_EXIT = "EOD_EXIT"            # forced exit at end of session
    MANUAL_CLOSE = "MANUAL_CLOSE"    # operator closed via API
    RISK_HALT = "RISK_HALT"          # risk manager halted trading
    BROKER_FORCED = "BROKER_FORCED"  # broker squared off (e.g. margin call)


class LivePosition(Document):
    """
    Open or closed live position.

    Collection: live_positions
    """

    position_id: str = Field(default_factory=_new_position_id)

    # Provenance — link to originating signal + the orders that opened/closed.
    signal_id: Optional[str] = Field(default=None, description="Source LiveSignal id")
    entry_order_id: Optional[str] = Field(
        default=None, description="LiveOrder.order_id that opened the position"
    )
    exit_order_id: Optional[str] = Field(
        default=None, description="LiveOrder.order_id that closed the position"
    )
    broker_name: str = Field(..., description="Broker that holds this position")

    # ── Position shape ───────────────────────────────────────────────────────
    symbol: str = Field(..., description="NSE ticker symbol")
    exchange: str = Field(default="NSE", description="Exchange code")
    trading_date: datetime = Field(..., description="Trading date (UTC midnight)")

    trade_side: LiveTradeSide = Field(..., description="LONG or SHORT")

    quantity: int = Field(..., description="Number of shares held (always positive)")
    average_price: float = Field(..., description="VWAP of entry fills")
    current_price: float = Field(..., description="Most recent mark price")
    stop_loss: float = Field(..., description="Configured stop-loss level")

    # ── P&L (₹) ──────────────────────────────────────────────────────────────
    unrealized_pnl: float = Field(default=0.0)
    realized_pnl: float = Field(default=0.0)

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    status: LivePositionStatus = Field(default=LivePositionStatus.OPEN)
    exit_reason: Optional[LiveExitReason] = Field(default=None)
    exit_price: Optional[float] = Field(default=None)

    opened_at: datetime = Field(default_factory=_utcnow)
    closed_at: Optional[datetime] = Field(default=None)
    updated_at: datetime = Field(default_factory=_utcnow)

    metadata: dict = Field(default_factory=dict)

    class Settings:
        name = "live_positions"
        indexes = [
            IndexModel([("position_id", ASCENDING)], unique=True, name="position_id_unique"),
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("trading_date", ASCENDING)]),
            IndexModel([("opened_at", DESCENDING)]),
            IndexModel(
                [("symbol", ASCENDING), ("trading_date", ASCENDING), ("status", ASCENDING)],
                name="symbol_date_status",
            ),
            IndexModel(
                [("broker_name", ASCENDING), ("status", ASCENDING)],
                name="broker_status_idx",
            ),
            IndexModel([("entry_order_id", ASCENDING)], sparse=True, name="entry_order_idx"),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()
