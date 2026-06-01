"""
Opening Range Historical Validation — Beanie document models.

Four strategy-specific MongoDB collections:

    orhv_setups           — Phase 1 detection results (per symbol per day)
    orhv_validations      — Phase 2 historical validation outcomes
    orhv_signals          — Phase 3 live signals generated for Day D+1
    orhv_statistics       — Rolling per-symbol aggregate statistics

Design rules:
  - All date/time fields stored as UTC-aware datetimes.
  - strategy_id is hard-coded so records are unambiguously traceable.
  - Optional fields allow partial population (e.g. CH1 found but CL1 not yet).
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional
from uuid import uuid4

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.strategy.strategies.opening_range_historical_validation.constants import (
    STRATEGY_ID,
    STRATEGY_NAME,
    STRATEGY_VERSION,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid4().hex


# ── Enums ─────────────────────────────────────────────────────────────────────

class ORHVSignalType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class ORHVSignalStatus(StrEnum):
    GENERATED = "GENERATED"
    BROADCAST = "BROADCAST"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


# ── Phase 1 — Setup Detection ─────────────────────────────────────────────────

class ORHVSetup(Document):
    """
    Phase 1 detection result for a single (symbol, Day D) pair.

    is_candidate=True means both Condition A and Condition B were satisfied
    on Day D — the stock is eligible for Phase 2 historical validation.

    Collection: orhv_setups
    Unique constraint: (symbol, setup_date)
    """

    setup_id: str = Field(default_factory=_new_id)
    symbol: str = Field(..., description="NSE ticker symbol")
    setup_date: datetime = Field(..., description="Day D — UTC midnight")

    # ── Opening Range (9:15–9:30 candle) ──────────────────────────────────────
    orh_d: float = Field(..., description="ORH on Day D — high of 9:15 candle")
    orl_d: float = Field(..., description="ORL on Day D — low of 9:15 candle")

    # ── CH1 — first candle whose HIGH exceeds ORH_D ───────────────────────────
    ch1_found: bool = Field(default=False)
    ch1_high: Optional[float] = Field(default=None, description="CH1.high — the new upper level")
    ch1_time: Optional[datetime] = Field(default=None, description="Timestamp of CH1 candle")

    # Condition A: a LATER candle closes ABOVE ch1_high
    condition_a_met: bool = Field(default=False)
    condition_a_time: Optional[datetime] = Field(default=None)
    condition_a_close: Optional[float] = Field(default=None, description="Close that confirmed A")

    # ── CL1 — first candle whose LOW falls below ORL_D ────────────────────────
    cl1_found: bool = Field(default=False)
    cl1_low: Optional[float] = Field(default=None, description="CL1.low — the new lower level")
    cl1_time: Optional[datetime] = Field(default=None, description="Timestamp of CL1 candle")

    # Condition B: a LATER candle closes BELOW cl1_low
    condition_b_met: bool = Field(default=False)
    condition_b_time: Optional[datetime] = Field(default=None)
    condition_b_close: Optional[float] = Field(default=None, description="Close that confirmed B")

    # ── Result ────────────────────────────────────────────────────────────────
    is_candidate: bool = Field(default=False, description="True when both A and B are met")
    rejection_reason: Optional[str] = Field(default=None)
    candle_count: int = Field(default=0)

    strategy_id: str = Field(default=STRATEGY_ID)
    strategy_name: str = Field(default=STRATEGY_NAME)

    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "orhv_setups"
        indexes = [
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("setup_date", DESCENDING)]),
            IndexModel([("is_candidate", ASCENDING)]),
            IndexModel([("strategy_id", ASCENDING)]),
            IndexModel(
                [("symbol", ASCENDING), ("setup_date", ASCENDING)],
                unique=True,
                name="orhv_symbol_date_unique",
            ),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()


# ── Phase 2 — Historical Validation ──────────────────────────────────────────

class ORHVValidationRecord(Document):
    """
    Phase 2 validation outcome for a candidate (symbol, Day D).

    Records the result of simulating the last N historical ORHV setups
    using Phase 3 rules to determine if the pattern is tradable for Day D+1.

    Collection: orhv_validations
    Unique constraint: (symbol, candidate_date)
    """

    validation_id: str = Field(default_factory=_new_id)
    symbol: str
    candidate_date: datetime = Field(..., description="Day D — the setup day (UTC midnight)")
    execution_date: datetime = Field(..., description="Day D+1 — the planned trade day (UTC midnight)")

    # ── Simulation summary ────────────────────────────────────────────────────
    occurrences_available: int = Field(default=0, description="Total prior setups found")
    occurrences_used: int = Field(default=0, description="Number used in validation (≤ lookback_occurrences)")
    wins: int = Field(default=0)
    losses: int = Field(default=0)
    win_rate: float = Field(default=0.0, description="wins / occurrences_used")
    avg_pnl: float = Field(default=0.0, description="Average P&L per simulated trade (₹)")
    total_pnl: float = Field(default=0.0)

    # ── Verdict ───────────────────────────────────────────────────────────────
    tradable: bool = Field(default=False)
    rejection_reason: Optional[str] = Field(default=None)

    # ── Per-occurrence trade details ──────────────────────────────────────────
    # Stored as a list of dicts for auditability without a separate collection.
    simulated_trades: list[dict] = Field(
        default_factory=list,
        description="One dict per simulated historical occurrence",
    )

    strategy_id: str = Field(default=STRATEGY_ID)

    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "orhv_validations"
        indexes = [
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("candidate_date", DESCENDING)]),
            IndexModel([("tradable", ASCENDING)]),
            IndexModel([("strategy_id", ASCENDING)]),
            IndexModel(
                [("symbol", ASCENDING), ("candidate_date", ASCENDING)],
                unique=True,
                name="orhv_val_symbol_date_unique",
            ),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()


# ── Phase 3 — Live Signal ─────────────────────────────────────────────────────

class ORHVSignalRecord(Document):
    """
    Phase 3 live signal generated for Day D+1.

    Created when the D+1 opening range filter passes AND the first breakout
    occurs before 12:00 IST.

    Collection: orhv_signals
    Unique constraint: (symbol, trading_date)  — one signal per stock per day
    """

    signal_id: str = Field(default_factory=_new_id)
    symbol: str
    trading_date: datetime = Field(..., description="Day D+1 — trade date (UTC midnight)")
    candidate_date: datetime = Field(..., description="Day D — setup detection date (UTC midnight)")

    # ── Signal ────────────────────────────────────────────────────────────────
    signal_type: ORHVSignalType = Field(..., description="BUY or SELL")
    signal_status: ORHVSignalStatus = Field(default=ORHVSignalStatus.GENERATED)

    # ── D+1 Opening Range ─────────────────────────────────────────────────────
    orh: float = Field(..., description="ORH on Day D+1 (first candle high)")
    orl: float = Field(..., description="ORL on Day D+1 (first candle low)")
    or_close: float = Field(..., description="Close of D+1 first candle")
    orb_range_pct: float = Field(..., description="(ORH-ORL)/or_close * 100")

    # ── Trade parameters ──────────────────────────────────────────────────────
    entry_price: float = Field(..., description="ORH (BUY) or ORL (SELL)")
    stop_loss: float = Field(..., description="ORL (BUY) or ORH (SELL)")
    breakout_time: datetime = Field(..., description="UTC timestamp of breakout candle")

    # ── Validation context ────────────────────────────────────────────────────
    win_rate: float = Field(..., description="Historical win rate from Phase 2")
    occurrences_used: int = Field(default=0)

    strategy_id: str = Field(default=STRATEGY_ID)
    strategy_name: str = Field(default=STRATEGY_NAME)
    strategy_version: str = Field(default=STRATEGY_VERSION)

    probability_score: float = Field(
        default=0.0,
        description="Alias of win_rate for compatibility with the dashboard",
    )

    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "orhv_signals"
        indexes = [
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("trading_date", DESCENDING)]),
            IndexModel([("signal_type", ASCENDING)]),
            IndexModel([("signal_status", ASCENDING)]),
            IndexModel([("strategy_id", ASCENDING)]),
            IndexModel([("signal_id", ASCENDING)], unique=True, name="orhv_signal_id_unique"),
            IndexModel(
                [("symbol", ASCENDING), ("trading_date", ASCENDING)],
                unique=True,
                name="orhv_signal_symbol_date_unique",
            ),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()


# ── Rolling statistics ────────────────────────────────────────────────────────

class ORHVStatistics(Document):
    """
    Rolling per-symbol performance statistics for the ORHV strategy.

    Updated after each validation run so the API can surface stock rankings
    and win-rate trends without re-querying individual validation records.

    Collection: orhv_statistics
    Unique constraint: symbol
    """

    symbol: str
    total_setups_detected: int = Field(default=0, description="All-time Phase 1 candidates")
    tradable_setups: int = Field(default=0, description="Phase 2 tradable count")
    tradable_rate: float = Field(default=0.0, description="tradable / total_setups")
    current_win_rate: float = Field(default=0.0, description="Most recent Phase 2 win rate")
    avg_historical_win_rate: float = Field(default=0.0, description="Mean win rate across all validations")
    last_setup_date: Optional[datetime] = Field(default=None)
    last_signal_date: Optional[datetime] = Field(default=None)
    last_calculated_at: datetime = Field(default_factory=_utcnow)

    strategy_id: str = Field(default=STRATEGY_ID)
    metadata: dict = Field(default_factory=dict)

    class Settings:
        name = "orhv_statistics"
        indexes = [
            IndexModel([("symbol", ASCENDING)], unique=True, name="orhv_stats_symbol_unique"),
            IndexModel([("current_win_rate", DESCENDING)]),
            IndexModel([("tradable_setups", DESCENDING)]),
            IndexModel([("strategy_id", ASCENDING)]),
        ]
