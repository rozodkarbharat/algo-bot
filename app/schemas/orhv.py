"""
Pydantic API schemas for ORHV strategy endpoints.

Decouples the HTTP API surface from MongoDB document structure.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Detection (Phase 1) ───────────────────────────────────────────────────────

class ORHVSetupResponse(BaseModel):
    setup_id: str
    symbol: str
    setup_date: date
    orh_d: float
    orl_d: float
    ch1_found: bool
    ch1_high: Optional[float]
    ch1_time: Optional[datetime]
    cl1_found: bool
    cl1_low: Optional[float]
    cl1_time: Optional[datetime]
    condition_a_met: bool
    condition_b_met: bool
    is_candidate: bool
    rejection_reason: Optional[str]
    candle_count: int
    created_at: datetime

    @classmethod
    def from_document(cls, doc) -> "ORHVSetupResponse":
        from app.utils.market_time import utc_midnight_to_date
        return cls(
            setup_id=doc.setup_id,
            symbol=doc.symbol,
            setup_date=utc_midnight_to_date(doc.setup_date),
            orh_d=doc.orh_d,
            orl_d=doc.orl_d,
            ch1_found=doc.ch1_found,
            ch1_high=doc.ch1_high,
            ch1_time=doc.ch1_time,
            cl1_found=doc.cl1_found,
            cl1_low=doc.cl1_low,
            cl1_time=doc.cl1_time,
            condition_a_met=doc.condition_a_met,
            condition_b_met=doc.condition_b_met,
            is_candidate=doc.is_candidate,
            rejection_reason=doc.rejection_reason,
            candle_count=doc.candle_count,
            created_at=doc.created_at,
        )


# ── Validation (Phase 2) ──────────────────────────────────────────────────────

class ORHVSimulatedTradeSummary(BaseModel):
    setup_date: str
    execution_date: str
    trade_side: Optional[str]
    entry_price: Optional[float]
    exit_price: Optional[float]
    stop_loss: Optional[float]
    pnl: float
    exit_reason: Optional[str]
    is_win: bool
    orb_range_pct: float


class ORHVValidationResponse(BaseModel):
    validation_id: str
    symbol: str
    candidate_date: date
    execution_date: date
    occurrences_available: int
    occurrences_used: int
    wins: int
    losses: int
    win_rate: float
    win_rate_pct: float
    avg_pnl: float
    total_pnl: float
    tradable: bool
    rejection_reason: Optional[str]
    simulated_trades: list[ORHVSimulatedTradeSummary] = Field(default_factory=list)
    created_at: datetime

    @classmethod
    def from_document(cls, doc) -> "ORHVValidationResponse":
        from app.utils.market_time import utc_midnight_to_date
        return cls(
            validation_id=doc.validation_id,
            symbol=doc.symbol,
            candidate_date=utc_midnight_to_date(doc.candidate_date),
            execution_date=utc_midnight_to_date(doc.execution_date),
            occurrences_available=doc.occurrences_available,
            occurrences_used=doc.occurrences_used,
            wins=doc.wins,
            losses=doc.losses,
            win_rate=doc.win_rate,
            win_rate_pct=round(doc.win_rate * 100, 2),
            avg_pnl=doc.avg_pnl,
            total_pnl=doc.total_pnl,
            tradable=doc.tradable,
            rejection_reason=doc.rejection_reason,
            simulated_trades=[
                ORHVSimulatedTradeSummary(**t)
                for t in doc.simulated_trades
            ],
            created_at=doc.created_at,
        )


# ── Signal (Phase 3) ──────────────────────────────────────────────────────────

class ORHVSignalResponse(BaseModel):
    signal_id: str
    symbol: str
    trading_date: date
    candidate_date: date
    signal_type: str
    signal_status: str
    entry_price: float
    stop_loss: float
    orh: float
    orl: float
    or_close: float
    orb_range_pct: float
    breakout_time: datetime
    win_rate: float
    win_rate_pct: float
    occurrences_used: int
    strategy_id: str
    created_at: datetime

    @classmethod
    def from_document(cls, doc) -> "ORHVSignalResponse":
        from app.utils.market_time import utc_midnight_to_date
        return cls(
            signal_id=doc.signal_id,
            symbol=doc.symbol,
            trading_date=utc_midnight_to_date(doc.trading_date),
            candidate_date=utc_midnight_to_date(doc.candidate_date),
            signal_type=doc.signal_type.value,
            signal_status=doc.signal_status.value,
            entry_price=doc.entry_price,
            stop_loss=doc.stop_loss,
            orh=doc.orh,
            orl=doc.orl,
            or_close=doc.or_close,
            orb_range_pct=doc.orb_range_pct,
            breakout_time=doc.breakout_time,
            win_rate=doc.win_rate,
            win_rate_pct=round(doc.win_rate * 100, 2),
            occurrences_used=doc.occurrences_used,
            strategy_id=doc.strategy_id,
            created_at=doc.created_at,
        )


# ── Statistics ────────────────────────────────────────────────────────────────

class ORHVStatisticsResponse(BaseModel):
    symbol: str
    total_setups_detected: int
    tradable_setups: int
    tradable_rate: float
    current_win_rate: float
    current_win_rate_pct: float
    avg_historical_win_rate: float
    last_setup_date: Optional[date]
    last_calculated_at: datetime

    @classmethod
    def from_document(cls, doc) -> "ORHVStatisticsResponse":
        from app.utils.market_time import utc_midnight_to_date
        return cls(
            symbol=doc.symbol,
            total_setups_detected=doc.total_setups_detected,
            tradable_setups=doc.tradable_setups,
            tradable_rate=round(doc.tradable_rate, 4),
            current_win_rate=doc.current_win_rate,
            current_win_rate_pct=round(doc.current_win_rate * 100, 2),
            avg_historical_win_rate=round(doc.avg_historical_win_rate * 100, 2),
            last_setup_date=(
                utc_midnight_to_date(doc.last_setup_date)
                if doc.last_setup_date else None
            ),
            last_calculated_at=doc.last_calculated_at,
        )


# ── Request schemas ───────────────────────────────────────────────────────────

class ORHVRunDetectionRequest(BaseModel):
    trading_date: date
    symbols: Optional[list[str]] = None


class ORHVRunValidationRequest(BaseModel):
    candidate_date: date
    symbols: Optional[list[str]] = None


class ORHVRunCycleRequest(BaseModel):
    trading_date: date
    symbols: Optional[list[str]] = None


# ── Summary responses ─────────────────────────────────────────────────────────

class ORHVDetectionSummaryResponse(BaseModel):
    total_symbols: int
    candidates_found: int
    rejected: int
    no_data: int
    failed_symbols: list[str]
    duration_seconds: float


class ORHVValidationSummaryResponse(BaseModel):
    total_candidates: int
    tradable: int
    not_tradable: int
    insufficient_history: int
    failed_symbols: list[str]
    duration_seconds: float


# ── Daily shortlist (UI) ──────────────────────────────────────────────────────

class ORHVShortlistEntryResponse(BaseModel):
    """One row on the ORHV shortlist tab — Phase 1 candidate + Phase 2 outcome."""

    symbol: str
    candidate_date: date = Field(..., description="Day D — two-sided breakout session")
    execution_date: date = Field(..., description="Day D+1 — trading session")
    orh_d: float
    orl_d: float
    orb_range_pct: float
    win_rate: float = Field(..., description="Historical win rate 0.0–1.0")
    win_rate_pct: float = Field(..., description="Win rate as percentage")
    wins: int = 0
    losses: int = 0
    occurrences_used: int = 0
    occurrences_available: int = 0
    is_candidate: bool = Field(
        False, description="True if the symbol passed Phase 1 (formed the ORHV pattern)"
    )
    tradable: bool = True
    reason_skipped: Optional[str] = None


class ORHVShortlistResponse(BaseModel):
    """Full ORHV shortlist for an execution date (mirrors /shortlist/today envelope)."""

    strategy_id: str = "opening_range_historical_validation"
    strategy_name: str = "Opening Range Historical Validation"
    trading_date: date = Field(..., description="Execution date (Day D+1)")
    candidate_date: date = Field(..., description="Setup date (Day D)")
    total_candidates: int
    total_phase1_scanned: int = Field(
        0,
        description="Symbols with Phase 1 detection stored (candidates + rejected)",
    )
    total_tradable: int
    threshold_win_rate_pct: float
    generated_at: datetime
    entries: list[ORHVShortlistEntryResponse]


class ORHVShortlistRunRequest(BaseModel):
    """Body for POST /api/v1/orhv/run."""

    target_date: Optional[date] = Field(
        default=None,
        description="Execution date (Day D+1). Defaults to next trading day after last completed session.",
    )
    full_pipeline: bool = Field(
        default=True,
        description="If True, sync Day D candles from Angel One, then detect + validate.",
    )
    win_rate_threshold: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional UI filter override (0.0–1.0); stored validations use strategy defaults.",
    )


class ORHVShortlistRunResponse(BaseModel):
    status: str
    target_date: date
    total_checked: int
    total_shortlisted: int
    duration_seconds: float
    full_pipeline: bool = False
    data_date: Optional[date] = None
    candles_synced: Optional[int] = None
    sync_failed_symbols: Optional[list[str]] = None
    candidates_found: Optional[int] = None
    validation_tradable: Optional[int] = None


class ORHVShortlistStatusResponse(BaseModel):
    running: bool
    last_status: str
    last_started_at: Optional[datetime] = None
    last_finished_at: Optional[datetime] = None
    last_target_date: Optional[date] = None
    last_total_checked: int = 0
    last_total_shortlisted: int = 0
    last_duration_seconds: Optional[float] = None
    last_error: Optional[str] = None
    last_trigger: Optional[str] = None


# ── Single-symbol tester ──────────────────────────────────────────────────────

class ORHVSymbolRunRequest(BaseModel):
    """Body for POST /api/v1/orhv/run-symbol."""

    symbol: str = Field(..., description="NSE ticker symbol to test")
    mode: str = Field(
        default="full",
        description="'full' = sync + detect + validate; 'phase2' = validate stored history only",
    )
    target_date: Optional[date] = Field(
        default=None,
        description="Execution date (Day D+1) for full mode, or candidate date (Day D) for phase2.",
    )


class ORHVSymbolRunResponse(BaseModel):
    symbol: str
    mode: str
    candidate_date: date
    execution_date: Optional[date] = None
    has_phase1_setup: bool
    is_candidate: bool
    phase1_reason: Optional[str] = None
    validated: bool
    occurrences_available: int
    occurrences_used: int
    wins: int
    losses: int
    win_rate: float
    win_rate_pct: float
    tradable: bool
    reason: Optional[str] = None
    orh_d: Optional[float] = None
    orl_d: Optional[float] = None
    candles_synced: int
    history_candle_days: int
    history_detection_days: int
    duration_seconds: float
    message: str
