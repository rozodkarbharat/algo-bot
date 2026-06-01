"""
Paper trading account document — single-account capital and tally state.

A PaperAccount is the authoritative source for available capital, used
capital, and aggregate P&L counters. Initially the system runs a single
account (`account_id="default"`), but the schema is multi-account ready
for future portfolio-simulation work.

Persistence design:
  - One row per `account_id`. Updates are upserts.
  - `realized_pnl` accumulates across sessions; `daily_pnl` is reset to 0
    by the SessionManager at start-of-day.
  - `is_paused` reflects either a manual pause or a risk-manager halt;
    the Paper Trading Service consults this flag before accepting signals.
"""

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


DEFAULT_PAPER_ACCOUNT_ID: str = "default"


class PaperAccount(Document):
    """
    Paper-trading account state.

    Collection: paper_accounts
    Unique constraint: account_id
    """

    account_id: str = Field(default=DEFAULT_PAPER_ACCOUNT_ID)

    starting_capital: float = Field(..., description="Initial virtual capital")
    available_capital: float = Field(..., description="Cash currently free to deploy")
    used_capital: float = Field(
        default=0.0, description="Capital locked in open paper positions"
    )

    realized_pnl: float = Field(default=0.0, description="Lifetime realised P&L (₹)")
    unrealized_pnl: float = Field(default=0.0, description="Sum of MTM P&L across open positions")
    daily_pnl: float = Field(
        default=0.0, description="Net P&L accumulated during the current trading day"
    )

    total_trades: int = Field(default=0)
    winning_trades: int = Field(default=0)
    losing_trades: int = Field(default=0)

    consecutive_losses: int = Field(
        default=0, description="Running count of consecutive losing trades"
    )

    is_paused: bool = Field(
        default=False,
        description="True when paper trading is halted (manual or risk-driven)",
    )
    pause_reason: Optional[str] = Field(default=None)

    last_reset_date: Optional[datetime] = Field(
        default=None, description="UTC midnight of the last daily reset"
    )

    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "paper_accounts"
        indexes = [
            IndexModel([("account_id", ASCENDING)], unique=True, name="account_id_unique"),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()
