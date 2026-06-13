"""
Pydantic API schemas for the strategy engine endpoints.

These are the HTTP contract (request/response shapes) for:
  - One-side day analysis results
  - Continuation statistics
  - Daily shortlist

The schemas deliberately decouple the API surface from the MongoDB document
structure — DB fields can evolve without breaking API consumers.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Request schemas ───────────────────────────────────────────────────────────

class RunDetectionRequest(BaseModel):
    """Request body for triggering historical OSD detection."""

    from_date: date = Field(..., description="Start of historical range (inclusive)")
    to_date: date = Field(..., description="End of historical range (inclusive)")
    symbols: Optional[list[str]] = Field(
        default=None,
        description="Symbols to process; omit for all active NIFTY50 stocks",
    )


class RunDetectionForDateRequest(BaseModel):
    """Request body for single-date OSD detection."""

    trading_date: date = Field(..., description="The trading date to classify")
    symbols: Optional[list[str]] = Field(default=None)


class RecalculateProbabilityRequest(BaseModel):
    """Request body for triggering continuation probability recalculation."""

    symbols: Optional[list[str]] = Field(
        default=None,
        description="Symbols to recalculate; omit for all active stocks",
    )
    lookback_days: Optional[int] = Field(
        default=None,
        ge=30,
        le=2520,
        description="Lookback window (trading days). Default: from settings.",
    )


# ── Response schemas ──────────────────────────────────────────────────────────

class OneSideDayResponse(BaseModel):
    """API representation of a single OneSideDay MongoDB document."""

    symbol: str
    trading_date: date
    is_one_side: bool
    direction: Optional[str] = Field(None, description="UP, DOWN, or null")
    first_candle_high: float
    first_candle_low: float
    breakout_price: Optional[float] = None
    breakout_time: Optional[datetime] = None
    move_percent: Optional[float] = None
    opposite_side_crossed: bool
    continuation_candidate: bool
    created_at: datetime

    @classmethod
    def from_document(cls, doc) -> "OneSideDayResponse":
        from app.utils.market_time import utc_midnight_to_date
        return cls(
            symbol=doc.symbol,
            trading_date=utc_midnight_to_date(doc.trading_date),
            is_one_side=doc.is_one_side,
            direction=doc.direction,
            first_candle_high=doc.first_candle_high,
            first_candle_low=doc.first_candle_low,
            breakout_price=doc.breakout_price,
            breakout_time=doc.breakout_time,
            move_percent=doc.move_percent,
            opposite_side_crossed=doc.opposite_side_crossed,
            continuation_candidate=doc.continuation_candidate,
            created_at=doc.created_at,
        )


class ContinuationStatResponse(BaseModel):
    """API representation of a ContinuationStatistic MongoDB document."""

    symbol: str
    total_occurrences: int
    continuation_successes: int
    continuation_failures: int
    continuation_probability: float = Field(
        ..., description="P(OneSideToday | OneSideYesterday) as 0.0–1.0"
    )
    continuation_probability_pct: float = Field(
        ..., description="Same probability as a percentage (0–100)"
    )
    tradable: bool
    lookback_days: int
    probability_threshold: float
    last_calculated_at: Optional[datetime] = None

    @classmethod
    def from_document(cls, doc) -> "ContinuationStatResponse":
        return cls(
            symbol=doc.symbol,
            total_occurrences=doc.total_occurrences,
            continuation_successes=doc.continuation_successes,
            continuation_failures=doc.continuation_failures,
            continuation_probability=doc.continuation_probability,
            continuation_probability_pct=round(doc.continuation_probability * 100, 2),
            tradable=doc.tradable,
            lookback_days=doc.lookback_days,
            probability_threshold=doc.probability_threshold,
            last_calculated_at=doc.last_calculated_at,
        )


class ShortlistEntryResponse(BaseModel):
    """A single tradable candidate in today's shortlist."""

    symbol: str
    direction: str = Field(..., description="BULLISH or BEARISH (UI); legacy UP/DOWN also accepted")
    # Legacy / analytics fields
    first_candle_high: float
    first_candle_low: float
    breakout_price: Optional[float] = None
    move_percent: Optional[float] = Field(None, description="Yesterday's % move from breakout")
    continuation_probability: float = Field(..., description="0.0–1.0")
    continuation_probability_pct: float = Field(..., description="Percentage 0–100")
    total_occurrences: int
    yesterday_date: date
    # Dashboard / Shortlist page fields
    orb_high: float = Field(..., description="Opening range high (first 15-min candle)")
    orb_low: float = Field(..., description="Opening range low (first 15-min candle)")
    entry_trigger: float = Field(..., description="Breakout entry level for next session")
    stop_loss: float = Field(..., description="ORB stop level opposite to trade direction")
    probability: float = Field(..., description="Continuation probability 0.0–1.0")
    first_candle_range_pct: float = Field(..., description="ORB range as % of low")
    tradable: bool = Field(default=True, description="Included in actionable shortlist")
    reason_skipped: Optional[str] = Field(default=None, description="Why excluded when tradable=False")


class ShortlistResponse(BaseModel):
    """Full daily shortlist response."""

    trading_date: date = Field(..., description="Session date this shortlist is for")
    target_date: date = Field(..., description="Alias of trading_date (API backward compat)")
    yesterday: date
    total_candidates: int = Field(
        ..., description="One-side stocks evaluated (yesterday's pool)"
    )
    total_tradable: int = Field(..., description="Stocks passing probability + tradable filters")
    total_checked: int = Field(..., description="Same as total_candidates")
    threshold_pct: float = Field(..., description="Probability threshold used (percentage)")
    generated_at: datetime = Field(..., description="When this shortlist was computed (UTC)")
    entries: list[ShortlistEntryResponse]


class ShortlistRunRequest(BaseModel):
    """Optional body for POST /api/v1/shortlist/run."""

    target_date: Optional[date] = Field(
        default=None,
        description=(
            "Date to generate the shortlist for. Defaults to the upcoming "
            "trading session (today before the 15:30 IST close, otherwise the "
            "next trading day)."
        ),
    )
    probability_threshold: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Override OSD_CONTINUATION_THRESHOLD (0.0–1.0).",
    )
    full_pipeline: bool = Field(
        default=True,
        description=(
            "If True (default), runs the full fallback pipeline: pulls candles "
            "from Angel One for the data date, runs OSD detection, recomputes "
            "continuation statistics, then generates the shortlist. "
            "If False, only the shortlist generation step runs against existing "
            "MongoDB data (fast read path, used by the 16:30 IST scheduler job)."
        ),
    )


class ShortlistRunResponse(BaseModel):
    """Result of a manual shortlist run."""

    status: str = Field(..., description="success | error")
    target_date: date
    total_checked: int = Field(..., description="Number of stocks evaluated")
    total_shortlisted: int = Field(..., description="Number of stocks added to the shortlist")
    duration_seconds: float
    threshold_pct: float

    # ── Optional fields populated only when full_pipeline=True ────────────────
    full_pipeline: bool = Field(
        default=False,
        description="Whether the full Angel One sync + detection pipeline was executed.",
    )
    data_date: Optional[date] = Field(
        default=None,
        description="Trading date whose candles were synced/classified (yesterday-of-target).",
    )
    candles_synced: Optional[int] = Field(
        default=None,
        description="Number of symbol day-buckets fetched from Angel One.",
    )
    sync_failed_symbols: Optional[list[str]] = Field(
        default=None,
        description="Symbols whose Angel One sync failed.",
    )
    osd_one_side_days: Optional[int] = Field(
        default=None,
        description="One-side days detected on data_date.",
    )
    tradable_symbols: Optional[int] = Field(
        default=None,
        description="Symbols flagged tradable after probability recalculation.",
    )


class ShortlistStatusResponse(BaseModel):
    """Current state of the shortlist run manager."""

    running: bool = Field(..., description="True if a run is currently in progress")
    last_status: str = Field(..., description="idle | running | success | error")
    last_started_at: Optional[datetime] = None
    last_finished_at: Optional[datetime] = None
    last_target_date: Optional[date] = None
    last_total_checked: int = 0
    last_total_shortlisted: int = 0
    last_duration_seconds: Optional[float] = None
    last_error: Optional[str] = None
    last_trigger: Optional[str] = Field(
        default=None, description="manual | scheduler"
    )


class DetectionSummaryResponse(BaseModel):
    """Summary returned after a bulk OSD detection run."""

    total_symbols: int
    total_days: int
    one_side_days: int
    choppy_days: int
    invalid_days: int
    records_written: int
    failed_symbols: list[str]
    duration_seconds: float


class ProbabilitySummaryResponse(BaseModel):
    """Summary returned after a probability recalculation run."""

    total_symbols: int
    tradable_symbols: int
    failed_symbols: list[str]
    duration_seconds: float
