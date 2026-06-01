"""
Portfolio risk state document.

One document per trading day — a running snapshot of the portfolio's
capital utilisation and risk exposure. Updated by the portfolio service
each time an allocation is approved or an execution is confirmed.

Unique constraint: trading_date.
"""

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PortfolioRiskState(Document):
    """
    Intraday risk snapshot for the portfolio layer.

    Collection: portfolio_risk_states
    Unique constraint: trading_date
    """

    # UTC midnight for the trading session date.
    trading_date: datetime = Field(..., description="UTC midnight of the trading session")

    # ── Capital summary ───────────────────────────────────────────────────────
    total_capital: float = Field(..., description="Total portfolio capital (₹)")
    used_capital: float = Field(
        default=0.0, description="Capital committed to approved allocations (₹)"
    )
    available_capital: float = Field(
        default=0.0, description="Capital available for new allocations (₹)"
    )

    # ── Risk metrics ──────────────────────────────────────────────────────────
    daily_risk_used: float = Field(
        default=0.0,
        description="Aggregate capital at risk across open approved allocations (₹)",
    )

    # ── Position counts ───────────────────────────────────────────────────────
    open_positions: int = Field(
        default=0, description="Number of currently APPROVED (active) allocations"
    )
    total_approved_today: int = Field(
        default=0, description="Total signals approved today (includes closed ones)"
    )
    total_rejected_today: int = Field(
        default=0, description="Total signals rejected today"
    )

    # ── Per-strategy breakdown ────────────────────────────────────────────────
    strategy_exposure: dict = Field(
        default_factory=dict,
        description="strategy_id -> allocated_capital (₹) for open positions",
    )

    # ── Per-sector breakdown ──────────────────────────────────────────────────
    sector_exposure: dict = Field(
        default_factory=dict,
        description="sector -> allocated_capital (₹) for open positions",
    )

    # ── Daily P&L tracking ─────────────────────────────────────────────────────
    realized_pnl_today: float = Field(
        default=0.0,
        description="Realized P&L from allocations closed today (₹)",
    )
    peak_capital_today: float = Field(
        default=0.0,
        description="Highest equity observed today (total_capital + realized_pnl)",
    )

    # ── Halt state ────────────────────────────────────────────────────────────
    is_halted: bool = Field(
        default=False,
        description="When True, all new allocations are blocked (daily loss limit hit)",
    )
    halt_reason: Optional[str] = Field(default=None)

    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "portfolio_risk_states"
        indexes = [
            IndexModel(
                [("trading_date", ASCENDING)],
                unique=True,
                name="trading_date_unique",
            ),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()
