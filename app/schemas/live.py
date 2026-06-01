"""
Pydantic API schemas for the live signal engine.

The schemas decouple the HTTP contract from the MongoDB document layout so the
underlying storage can evolve without breaking API consumers.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.intraday_market_state import IntradayBreakoutSide
from app.models.live_signal import LiveBreakoutSide, LiveSignalStatus, LiveSignalType


# ── Response schemas ──────────────────────────────────────────────────────────

class LiveSignalResponse(BaseModel):
    """API representation of a LiveSignal document."""

    signal_id: str
    symbol: str
    trading_date: date
    signal_type: LiveSignalType
    signal_status: LiveSignalStatus
    breakout_side: LiveBreakoutSide
    entry_price: float
    stop_loss: float
    first_candle_high: float
    first_candle_low: float
    orb_range_percent: float
    breakout_time: datetime
    probability_score: Optional[float] = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, doc) -> "LiveSignalResponse":
        from app.utils.market_time import utc_midnight_to_date
        return cls(
            signal_id=doc.signal_id,
            symbol=doc.symbol,
            trading_date=utc_midnight_to_date(doc.trading_date),
            signal_type=doc.signal_type,
            signal_status=doc.signal_status,
            breakout_side=doc.breakout_side,
            entry_price=doc.entry_price,
            stop_loss=doc.stop_loss,
            first_candle_high=doc.first_candle_high,
            first_candle_low=doc.first_candle_low,
            orb_range_percent=doc.orb_range_percent,
            breakout_time=doc.breakout_time,
            probability_score=doc.probability_score,
            metadata=doc.metadata,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )


class IntradayMarketStateResponse(BaseModel):
    """API representation of an IntradayMarketState document."""

    symbol: str
    trading_date: date
    first_candle_completed: bool
    first_candle_high: Optional[float] = None
    first_candle_low: Optional[float] = None
    first_candle_range_percent: Optional[float] = None
    breakout_detected: bool
    breakout_side: Optional[IntradayBreakoutSide] = None
    signal_generated: bool
    trade_locked: bool
    signal_id: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    updated_at: datetime

    @classmethod
    def from_document(cls, doc) -> "IntradayMarketStateResponse":
        from app.utils.market_time import utc_midnight_to_date
        return cls(
            symbol=doc.symbol,
            trading_date=utc_midnight_to_date(doc.trading_date),
            first_candle_completed=doc.first_candle_completed,
            first_candle_high=doc.first_candle_high,
            first_candle_low=doc.first_candle_low,
            first_candle_range_percent=doc.first_candle_range_percent,
            breakout_detected=doc.breakout_detected,
            breakout_side=doc.breakout_side,
            signal_generated=doc.signal_generated,
            trade_locked=doc.trade_locked,
            signal_id=doc.signal_id,
            metadata=doc.metadata,
            updated_at=doc.updated_at,
        )


class LiveEngineStatusResponse(BaseModel):
    """High-level health of the live engine."""

    running: bool
    subscribed_symbols: list[str]
    signals_today: int
    trade_locked_today: int
    session_active: bool = Field(
        ..., description="True when the engine accepts new entries (09:30–11:30 IST)"
    )
    market_open: bool = Field(..., description="True during NSE regular session hours")
    last_started_at: Optional[datetime] = None
    last_stopped_at: Optional[datetime] = None


class StartLiveEngineResponse(BaseModel):
    """Response returned after starting the live engine."""

    started: bool
    subscribed_symbols: list[str]
    trading_date: date
    message: str


class StopLiveEngineResponse(BaseModel):
    """Response returned after stopping the live engine."""

    stopped: bool
    signals_generated: int
    duration_seconds: float
    message: str


class LiveHealthResponse(BaseModel):
    """Health snapshot of the live engine — surfaces failure-mode signals."""

    status: str = Field(..., description="OK | DEGRADED | STALE | OFFLINE")
    running: bool
    market_open: bool
    entry_window_open: bool
    reconnect_count: int
    ticks_received: int
    ticks_dropped: int
    candles_emitted: int
    signals_emitted: int
    last_tick_at: Optional[datetime] = None
    last_candle_at: Optional[datetime] = None
    seconds_since_last_tick: Optional[float] = None
    seconds_since_last_candle: Optional[float] = None
    watchlist_size: int
    stale_symbols: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
