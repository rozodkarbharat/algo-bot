"""
Live intraday trading signal document.

One document per generated live signal. The Live Signal Engine writes a
`LiveSignal` whenever a breakout is confirmed for a shortlisted stock during
the intraday session — strictly signal generation, no order execution.

Strategy reference:
  - The signal is the result of an ORB breakout close above (BUY) or below
    (SELL) the first 15-min candle range, when the ORB range filter and time
    filters pass.
  - Stop loss is the opposite side of the opening range.
  - Exactly one signal can be emitted per (symbol, trading_date); duplicate
    inserts are blocked by the unique index `(symbol, trading_date)`.

Persistence contract:
  - `signal_id`  — application-generated short id (UUID4 hex). Always unique.
  - `trading_date` is stored as UTC midnight (matches the OneSideDay convention).
  - `breakout_time` is a UTC-aware timestamp of the candle that triggered entry.
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


def _new_signal_id() -> str:
    """Compact unique signal id; URL/log friendly."""
    return uuid4().hex


class LiveSignalType(StrEnum):
    """Direction of the generated signal."""

    BUY = "BUY"
    SELL = "SELL"


class LiveSignalStatus(StrEnum):
    """
    Lifecycle of a live signal.

    GENERATED  — fresh signal emitted by the engine (default).
    BROADCAST  — broadcast to WebSocket subscribers (informational marker).
    EXPIRED    — emitted but never actioned within the session (future use).
    CANCELLED  — manually invalidated (admin tooling, kill switch).
    """

    GENERATED = "GENERATED"
    BROADCAST = "BROADCAST"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class LiveBreakoutSide(StrEnum):
    """Which side of the opening range was broken."""

    UP = "UP"
    DOWN = "DOWN"


class LiveSignal(Document):
    """
    Persisted live intraday signal.

    Collection: live_signals
    Unique constraint: (symbol, trading_date)  — enforces the
    one-trade-per-stock-per-day rule at the database level.
    """

    # Application-generated id (separate from MongoDB _id).
    signal_id: str = Field(default_factory=_new_signal_id)

    symbol: str = Field(..., description="NSE ticker symbol")

    # UTC midnight for the trading session date (consistent with OneSideDay).
    trading_date: datetime = Field(..., description="Trading date (UTC midnight)")

    signal_type: LiveSignalType = Field(..., description="BUY or SELL")
    signal_status: LiveSignalStatus = Field(default=LiveSignalStatus.GENERATED)
    breakout_side: LiveBreakoutSide = Field(..., description="UP or DOWN")

    # Pricing
    entry_price: float = Field(..., description="Breakout candle close price")
    stop_loss: float = Field(..., description="Opposite side of the opening range")

    # Opening range (first 15-min candle)
    first_candle_high: float = Field(..., description="ORB high")
    first_candle_low: float = Field(..., description="ORB low")
    orb_range_percent: float = Field(
        ..., description="(ORB high - ORB low) / ORB low * 100"
    )

    breakout_time: datetime = Field(
        ..., description="UTC timestamp of the candle that confirmed breakout"
    )

    # Probability provided by the research/shortlist engine. Optional so
    # signals generated without a shortlisted candidate (manual / backfill)
    # are still representable.
    probability_score: Optional[float] = Field(
        default=None,
        description="Continuation probability (0.0–1.0) from the research engine",
    )

    # ── Multi-strategy fields ─────────────────────────────────────────────────
    # Defaulted for backward compatibility: existing data without this field
    # is treated as belonging to the One-Side ORB strategy.
    strategy_id: str = Field(
        default="one_side_orb",
        description="Strategy that generated this signal",
    )
    strategy_name: str = Field(
        default="One-Side ORB",
        description="Human-readable strategy name",
    )
    strategy_version: str = Field(
        default="1.0.0",
        description="Strategy version at signal generation time",
    )

    metadata: dict = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "live_signals"
        indexes = [
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("trading_date", ASCENDING)]),
            IndexModel([("signal_status", ASCENDING)]),
            IndexModel([("breakout_time", DESCENDING)]),
            IndexModel([("strategy_id", ASCENDING)]),
            # Application-generated id used in API URLs/logs.
            IndexModel([("signal_id", ASCENDING)], unique=True, name="signal_id_unique"),
            # Enforces the one-signal-per-stock-per-day-per-strategy invariant.
            # Includes strategy_id so multiple strategies can signal the same
            # stock on the same day (each strategy gets its own quota of 1).
            IndexModel(
                [
                    ("symbol", ASCENDING),
                    ("trading_date", ASCENDING),
                    ("strategy_id", ASCENDING),
                ],
                unique=True,
                name="symbol_date_strategy_unique",
            ),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()
