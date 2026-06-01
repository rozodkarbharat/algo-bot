"""
Paper trading PnL engine — pure pricing maths for paper positions and trades.

This module is intentionally stateless and broker-independent. It performs
look-up-free arithmetic that the Position Manager, Risk Manager, Session
Manager and the API layer can all rely on without touching MongoDB or the
broker.

Functions are written to operate on either a `PaperPosition` document or
raw float inputs so they remain easy to unit-test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.models.paper_account import PaperAccount
from app.models.paper_position import PaperPosition, PaperTradeSide
from app.models.paper_trade import PaperTrade


# ── Per-position helpers ─────────────────────────────────────────────────────

def calculate_unrealized_pnl(
    trade_side: PaperTradeSide,
    quantity: int,
    entry_price: float,
    current_price: float,
) -> float:
    """
    Return mark-to-market P&L for an open position.

    LONG  : (current - entry) * qty
    SHORT : (entry  - current) * qty

    Brokerage is NOT subtracted here — unrealized P&L tracks raw market
    move only. Brokerage is realised on close.
    """
    if trade_side is PaperTradeSide.LONG:
        return (current_price - entry_price) * quantity
    return (entry_price - current_price) * quantity


def calculate_realized_pnl(
    trade_side: PaperTradeSide,
    quantity: int,
    entry_price: float,
    exit_price: float,
    brokerage_total: float,
) -> float:
    """
    Return net realised P&L after subtracting total round-trip brokerage.

    `brokerage_total` should be the entry-side + exit-side brokerage (₹).
    """
    if trade_side is PaperTradeSide.LONG:
        gross = (exit_price - entry_price) * quantity
    else:
        gross = (entry_price - exit_price) * quantity
    return round(gross - brokerage_total, 4)


def calculate_pnl_percent(pnl: float, capital_used: float) -> float:
    """
    Return P&L as a percentage of capital deployed.

    Defensive against zero capital — returns 0.0 if capital_used <= 0.
    """
    if capital_used <= 0:
        return 0.0
    return round((pnl / capital_used) * 100.0, 4)


def capital_used_for_position(position: PaperPosition) -> float:
    """Capital tied up by an open position (₹). LONG and SHORT are symmetric."""
    return position.entry_price * position.quantity


# ── Aggregate helpers (account-level) ────────────────────────────────────────

@dataclass(frozen=True)
class PnLAggregate:
    """Aggregate P&L snapshot across many positions / trades."""

    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float


def aggregate_unrealized(positions: Iterable[PaperPosition]) -> float:
    """Sum MTM P&L across an iterable of open positions."""
    return round(sum(p.unrealized_pnl for p in positions), 4)


def aggregate_realized(trades: Iterable[PaperTrade]) -> float:
    """Sum net P&L across an iterable of closed trades."""
    return round(sum(t.pnl for t in trades), 4)


def aggregate_pnl(
    open_positions: Iterable[PaperPosition],
    closed_trades: Iterable[PaperTrade],
) -> PnLAggregate:
    """Return the combined realized + unrealized snapshot."""
    realized = aggregate_realized(closed_trades)
    unrealized = aggregate_unrealized(open_positions)
    return PnLAggregate(
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        total_pnl=round(realized + unrealized, 4),
    )


# ── Equity-curve point generation ────────────────────────────────────────────

@dataclass(frozen=True)
class EquityPoint:
    """A single point on the equity curve."""

    timestamp_iso: str
    equity: float
    cumulative_pnl: float


def equity_curve_from_trades(
    starting_capital: float, trades: list[PaperTrade]
) -> list[EquityPoint]:
    """
    Reconstruct the equity curve from a list of closed trades (chronological).

    Each point is anchored to the trade's `closed_at`. The starting point is
    `(min(opened_at), starting_capital)` if any trades exist; otherwise the
    list is empty.
    """
    if not trades:
        return []
    sorted_trades = sorted(trades, key=lambda t: t.closed_at)
    equity = starting_capital
    cumulative = 0.0
    curve: list[EquityPoint] = []
    for t in sorted_trades:
        cumulative = round(cumulative + t.pnl, 4)
        equity = round(equity + t.pnl, 4)
        curve.append(
            EquityPoint(
                timestamp_iso=t.closed_at.isoformat(),
                equity=equity,
                cumulative_pnl=cumulative,
            )
        )
    return curve


# ── ROI helpers (account-level) ──────────────────────────────────────────────

def roi_percent(starting_capital: float, total_pnl: float) -> float:
    """Return cumulative ROI as a percentage of starting capital."""
    if starting_capital <= 0:
        return 0.0
    return round((total_pnl / starting_capital) * 100.0, 4)


def apply_realized_pnl_to_account(
    account: PaperAccount, trade: PaperTrade
) -> PaperAccount:
    """
    Update the account in place after a trade closes.

    Returns the same `account` instance with:
      - available_capital restored (entry capital + pnl returned)
      - used_capital decremented
      - realized_pnl / daily_pnl incremented
      - trade tallies and consecutive-loss counter updated
    """
    capital_returned = trade.entry_price * trade.quantity + trade.pnl
    account.available_capital = round(account.available_capital + capital_returned, 4)
    account.used_capital = round(
        max(0.0, account.used_capital - trade.entry_price * trade.quantity), 4
    )
    account.realized_pnl = round(account.realized_pnl + trade.pnl, 4)
    account.daily_pnl = round(account.daily_pnl + trade.pnl, 4)
    account.total_trades += 1
    if trade.pnl > 0:
        account.winning_trades += 1
        account.consecutive_losses = 0
    else:
        account.losing_trades += 1
        account.consecutive_losses += 1
    account.mark_updated()
    return account


def apply_entry_to_account(account: PaperAccount, capital_used: float) -> PaperAccount:
    """Lock entry capital on the account when a paper position is opened."""
    account.available_capital = round(account.available_capital - capital_used, 4)
    account.used_capital = round(account.used_capital + capital_used, 4)
    account.mark_updated()
    return account


def refresh_unrealized_on_account(
    account: PaperAccount, open_positions: Iterable[PaperPosition]
) -> PaperAccount:
    """Recompute account.unrealized_pnl from the live open positions."""
    account.unrealized_pnl = aggregate_unrealized(open_positions)
    account.mark_updated()
    return account
