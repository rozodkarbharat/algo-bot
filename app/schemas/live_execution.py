"""
Pydantic API schemas for the live execution subsystem.

These schemas keep the HTTP contract decoupled from the MongoDB document
shape — internal storage can evolve without breaking the dashboard.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.live_order import LiveOrderStatus, LiveOrderType, LiveTradeSide
from app.models.live_position import LiveExitReason, LivePositionStatus


# ── Response: Orders ─────────────────────────────────────────────────────────

class LiveOrderResponse(BaseModel):
    """API representation of a LiveOrder document."""

    order_id: str
    broker_order_id: Optional[str] = None
    signal_id: Optional[str] = None
    broker_name: str
    symbol: str
    exchange: str
    order_type: LiveOrderType
    trade_side: LiveTradeSide
    quantity: int
    filled_quantity: int
    requested_price: Optional[float] = None
    executed_price: Optional[float] = None
    stop_loss: Optional[float] = None
    order_status: LiveOrderStatus
    rejection_reason: Optional[str] = None
    slippage: float
    brokerage: float
    trading_date: date
    created_at: datetime
    updated_at: datetime
    transitions: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    @classmethod
    def from_document(cls, doc) -> "LiveOrderResponse":
        from app.utils.market_time import utc_midnight_to_date
        return cls(
            order_id=doc.order_id,
            broker_order_id=doc.broker_order_id,
            signal_id=doc.signal_id,
            broker_name=doc.broker_name,
            symbol=doc.symbol,
            exchange=doc.exchange,
            order_type=doc.order_type,
            trade_side=doc.trade_side,
            quantity=doc.quantity,
            filled_quantity=doc.filled_quantity,
            requested_price=doc.requested_price,
            executed_price=doc.executed_price,
            stop_loss=doc.stop_loss,
            order_status=doc.order_status,
            rejection_reason=doc.rejection_reason,
            slippage=doc.slippage,
            brokerage=doc.brokerage,
            trading_date=utc_midnight_to_date(doc.trading_date),
            created_at=doc.created_at,
            updated_at=doc.updated_at,
            transitions=doc.transitions,
            metadata=doc.metadata,
        )


# ── Response: Positions ──────────────────────────────────────────────────────

class LivePositionResponse(BaseModel):
    """API representation of a LivePosition document."""

    position_id: str
    broker_name: str
    signal_id: Optional[str] = None
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    symbol: str
    exchange: str
    trading_date: date
    trade_side: LiveTradeSide
    quantity: int
    average_price: float
    current_price: float
    stop_loss: float
    unrealized_pnl: float
    realized_pnl: float
    status: LivePositionStatus
    exit_reason: Optional[LiveExitReason] = None
    exit_price: Optional[float] = None
    opened_at: datetime
    closed_at: Optional[datetime] = None
    updated_at: datetime
    metadata: dict = Field(default_factory=dict)

    @classmethod
    def from_document(cls, doc) -> "LivePositionResponse":
        from app.utils.market_time import utc_midnight_to_date
        return cls(
            position_id=doc.position_id,
            broker_name=doc.broker_name,
            signal_id=doc.signal_id,
            entry_order_id=doc.entry_order_id,
            exit_order_id=doc.exit_order_id,
            symbol=doc.symbol,
            exchange=doc.exchange,
            trading_date=utc_midnight_to_date(doc.trading_date),
            trade_side=doc.trade_side,
            quantity=doc.quantity,
            average_price=doc.average_price,
            current_price=doc.current_price,
            stop_loss=doc.stop_loss,
            unrealized_pnl=doc.unrealized_pnl,
            realized_pnl=doc.realized_pnl,
            status=doc.status,
            exit_reason=doc.exit_reason,
            exit_price=doc.exit_price,
            opened_at=doc.opened_at,
            closed_at=doc.closed_at,
            updated_at=doc.updated_at,
            metadata=doc.metadata,
        )


# ── Response: PnL snapshot ───────────────────────────────────────────────────

class LivePnLResponse(BaseModel):
    """Live execution engine snapshot."""

    enabled: bool
    kill_switch: dict
    open_positions: int
    total_exposure: float
    realized_pnl_today: float
    unrealized_pnl: float
    daily_pnl: float
    total_capital: float
    peak_equity: float
    current_equity: float
    trades_today: int
    is_paused: bool
    pause_reason: Optional[str] = None
    broker_session_healthy: bool
    updated_at: str


# ── Request / response bodies for control endpoints ──────────────────────────

class LivePauseRequest(BaseModel):
    reason: Optional[str] = Field(
        default="manual_pause",
        description="Free-form reason recorded on the engine state",
    )


class LiveEmergencyStopRequest(BaseModel):
    reason: Optional[str] = Field(
        default="operator_emergency_stop",
        description="Free-form reason recorded with the kill-switch event",
    )


class LiveControlResponse(BaseModel):
    """Generic response body for pause/resume/kill-switch endpoints."""

    enabled: bool
    is_paused: bool
    pause_reason: Optional[str] = None
    kill_switch: dict
    message: str


class LiveCloseAllResponse(BaseModel):
    closed: int
    reason: str
    message: str
