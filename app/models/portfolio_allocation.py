"""
Portfolio allocation document.

One document per signal that enters the portfolio layer. Represents the
outcome of the signal ranking → capital allocation → risk gate pipeline for
a single live signal. The portfolio service writes this document before
dispatching approved signals to paper / live execution.

Unique constraint: (signal_id) — one allocation row per signal.
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


def _new_allocation_id() -> str:
    return uuid4().hex


class AllocationStatus(StrEnum):
    """Lifecycle states of a portfolio allocation."""

    APPROVED = "APPROVED"    # passed ranking + risk; dispatched to execution
    REJECTED = "REJECTED"    # blocked by risk manager or capital exhaustion
    EXECUTED = "EXECUTED"    # execution layer confirmed a fill
    CANCELLED = "CANCELLED"  # manually invalidated post-approval


class AllocationMethod(StrEnum):
    """Capital allocation algorithm used to size this trade."""

    EQUAL_WEIGHT = "EQUAL_WEIGHT"
    SCORE_WEIGHTED = "SCORE_WEIGHTED"
    FIXED_RISK = "FIXED_RISK"


class PortfolioAllocation(Document):
    """
    Persisted outcome of the portfolio allocation pipeline for one signal.

    Collection: portfolio_allocations
    Unique constraint: signal_id
    """

    allocation_id: str = Field(default_factory=_new_allocation_id)

    # ── Session identity ──────────────────────────────────────────────────────
    trading_date: datetime = Field(..., description="UTC midnight of the trading session")
    strategy_id: str = Field(..., description="Strategy that emitted the source signal")
    symbol: str = Field(..., description="NSE ticker symbol")

    # ── Signal reference ──────────────────────────────────────────────────────
    signal_id: str = Field(..., description="signal_id from the source LiveSignal")
    signal_type: str = Field(..., description="BUY or SELL")
    entry_price: float = Field(..., description="Signal entry price")
    stop_loss: float = Field(..., description="Signal stop loss")
    probability_score: Optional[float] = Field(
        default=None, description="Continuation probability from the shortlist engine"
    )

    # ── Ranking output ────────────────────────────────────────────────────────
    ranking_score: float = Field(
        default=0.0, description="Composite ranking score [0.0, 1.0]"
    )
    ranking_components: dict = Field(
        default_factory=dict,
        description="Per-factor breakdown used to compute ranking_score",
    )

    # ── Capital allocation output ─────────────────────────────────────────────
    allocation_method: AllocationMethod = Field(
        default=AllocationMethod.EQUAL_WEIGHT,
        description="Algorithm used to compute allocated_capital",
    )
    allocation_percent: float = Field(
        default=0.0,
        description="Fraction of total portfolio capital assigned to this trade [0, 1]",
    )
    allocated_capital: float = Field(
        default=0.0, description="Absolute capital assigned to this trade (₹)"
    )

    # ── Risk gate outcome ─────────────────────────────────────────────────────
    allocation_status: AllocationStatus = Field(default=AllocationStatus.REJECTED)
    rejection_reason: Optional[str] = Field(
        default=None, description="Set when allocation_status == REJECTED"
    )
    risk_detail: dict = Field(
        default_factory=dict, description="Diagnostic detail from the risk gate"
    )

    # ── Stock context ─────────────────────────────────────────────────────────
    sector: Optional[str] = Field(default=None, description="GICS sector of the symbol")

    metadata: dict = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "portfolio_allocations"
        indexes = [
            IndexModel([("trading_date", ASCENDING)]),
            IndexModel([("strategy_id", ASCENDING)]),
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("allocation_status", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
            IndexModel(
                [("signal_id", ASCENDING)],
                unique=True,
                name="signal_id_unique",
            ),
            IndexModel(
                [("trading_date", ASCENDING), ("allocation_status", ASCENDING)],
                name="date_status",
            ),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()
