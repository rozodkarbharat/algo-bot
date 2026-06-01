"""
Pydantic API schemas for the paper trading subsystem.

The schemas keep the HTTP contract decoupled from the MongoDB document
shape — fields can be renamed or restructured in storage without
breaking dashboard clients.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.paper_position import PaperPositionStatus, PaperTradeSide
from app.models.paper_trade import PaperExitReason


# ── Response: Positions ──────────────────────────────────────────────────────

class PaperPositionResponse(BaseModel):
    """API representation of a PaperPosition document."""

    position_id: str
    symbol: str
    trading_date: date
    trade_side: PaperTradeSide
    quantity: int
    entry_price: float
    current_price: float
    stop_loss: float
    unrealized_pnl: float
    realized_pnl: float
    status: PaperPositionStatus
    signal_id: Optional[str] = None
    opened_at: datetime
    closed_at: Optional[datetime] = None
    metadata: dict = Field(default_factory=dict)
    updated_at: datetime

    @classmethod
    def from_document(cls, doc) -> "PaperPositionResponse":
        from app.utils.market_time import utc_midnight_to_date
        return cls(
            position_id=doc.position_id,
            symbol=doc.symbol,
            trading_date=utc_midnight_to_date(doc.trading_date),
            trade_side=doc.trade_side,
            quantity=doc.quantity,
            entry_price=doc.entry_price,
            current_price=doc.current_price,
            stop_loss=doc.stop_loss,
            unrealized_pnl=doc.unrealized_pnl,
            realized_pnl=doc.realized_pnl,
            status=doc.status,
            signal_id=doc.signal_id,
            opened_at=doc.opened_at,
            closed_at=doc.closed_at,
            metadata=doc.metadata,
            updated_at=doc.updated_at,
        )


# ── Response: Trades ─────────────────────────────────────────────────────────

class PaperTradeResponse(BaseModel):
    """API representation of a PaperTrade document."""

    trade_id: str
    position_id: str
    signal_id: Optional[str] = None
    symbol: str
    trading_date: date
    trade_side: PaperTradeSide
    quantity: int
    entry_price: float
    exit_price: float
    stop_loss: float
    exit_reason: PaperExitReason
    slippage: float
    brokerage: float
    pnl: float
    pnl_percent: float
    opened_at: datetime
    closed_at: datetime
    metadata: dict = Field(default_factory=dict)
    created_at: datetime

    @classmethod
    def from_document(cls, doc) -> "PaperTradeResponse":
        from app.utils.market_time import utc_midnight_to_date
        return cls(
            trade_id=doc.trade_id,
            position_id=doc.position_id,
            signal_id=doc.signal_id,
            symbol=doc.symbol,
            trading_date=utc_midnight_to_date(doc.trading_date),
            trade_side=doc.trade_side,
            quantity=doc.quantity,
            entry_price=doc.entry_price,
            exit_price=doc.exit_price,
            stop_loss=doc.stop_loss,
            exit_reason=doc.exit_reason,
            slippage=doc.slippage,
            brokerage=doc.brokerage,
            pnl=doc.pnl,
            pnl_percent=doc.pnl_percent,
            opened_at=doc.opened_at,
            closed_at=doc.closed_at,
            metadata=doc.metadata,
            created_at=doc.created_at,
        )


# ── Response: Account ────────────────────────────────────────────────────────

class PaperAccountResponse(BaseModel):
    """API representation of the PaperAccount row."""

    account_id: str
    starting_capital: float
    available_capital: float
    used_capital: float
    realized_pnl: float
    unrealized_pnl: float
    daily_pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    consecutive_losses: int
    is_paused: bool
    pause_reason: Optional[str] = None
    last_reset_date: Optional[date] = None
    updated_at: datetime

    @classmethod
    def from_document(cls, doc) -> "PaperAccountResponse":
        from app.utils.market_time import utc_midnight_to_date
        return cls(
            account_id=doc.account_id,
            starting_capital=doc.starting_capital,
            available_capital=doc.available_capital,
            used_capital=doc.used_capital,
            realized_pnl=doc.realized_pnl,
            unrealized_pnl=doc.unrealized_pnl,
            daily_pnl=doc.daily_pnl,
            total_trades=doc.total_trades,
            winning_trades=doc.winning_trades,
            losing_trades=doc.losing_trades,
            consecutive_losses=doc.consecutive_losses,
            is_paused=doc.is_paused,
            pause_reason=doc.pause_reason,
            last_reset_date=(
                utc_midnight_to_date(doc.last_reset_date)
                if doc.last_reset_date is not None
                else None
            ),
            updated_at=doc.updated_at,
        )


# ── Response: PnL snapshot ───────────────────────────────────────────────────

class PaperPnLResponse(BaseModel):
    """Live PnL snapshot returned by GET /paper/pnl."""

    account_id: str
    starting_capital: float
    available_capital: float
    used_capital: float
    realized_pnl: float
    unrealized_pnl: float
    daily_pnl: float
    total_pnl: float
    roi_percent: float
    open_positions: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    consecutive_losses: int
    is_paused: bool
    pause_reason: Optional[str] = None
    updated_at: datetime


# ── Request / response bodies for control endpoints ──────────────────────────

class PaperPauseRequest(BaseModel):
    reason: Optional[str] = Field(
        default="manual_pause",
        description="Free-form reason recorded on the account row",
    )


class PaperPauseResponse(BaseModel):
    is_paused: bool
    pause_reason: Optional[str] = None
    message: str


class PaperResetResponse(BaseModel):
    account_id: str
    starting_capital: float
    available_capital: float
    message: str
