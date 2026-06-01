"""
Unit tests for the paper PnL engine.

Pure arithmetic — no I/O. Covers:
  - LONG / SHORT unrealized P&L direction
  - Realized P&L incorporates brokerage
  - PnL percent guards against zero capital
  - Aggregate helpers + equity curve generation
  - Account-mutation helpers
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.paper_account import PaperAccount
from app.models.paper_position import PaperPosition, PaperPositionStatus, PaperTradeSide
from app.models.paper_trade import PaperExitReason, PaperTrade
from app.paper_trading.pnl_engine import (
    aggregate_pnl,
    apply_entry_to_account,
    apply_realized_pnl_to_account,
    calculate_pnl_percent,
    calculate_realized_pnl,
    calculate_unrealized_pnl,
    equity_curve_from_trades,
    refresh_unrealized_on_account,
    roi_percent,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _position(
    side: PaperTradeSide = PaperTradeSide.LONG,
    entry: float = 100.0,
    current: float = 100.0,
    qty: int = 10,
) -> PaperPosition:
    now = datetime(2024, 6, 3, 4, 0, 0, tzinfo=timezone.utc)
    return PaperPosition.model_construct(
        position_id="pos-" + str(id(now)),
        symbol="RELIANCE",
        trading_date=datetime(2024, 6, 3, tzinfo=timezone.utc),
        trade_side=side,
        quantity=qty,
        entry_price=entry,
        current_price=current,
        stop_loss=entry - 2 if side is PaperTradeSide.LONG else entry + 2,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        status=PaperPositionStatus.OPEN,
        signal_id=None,
        opened_at=now,
        closed_at=None,
        metadata={},
        updated_at=now,
    )


def _trade(pnl: float, qty: int = 10) -> PaperTrade:
    now = datetime(2024, 6, 3, 8, 0, 0, tzinfo=timezone.utc)
    return PaperTrade.model_construct(
        trade_id="trade-" + str(id(now)),
        position_id="x",
        signal_id=None,
        symbol="RELIANCE",
        trading_date=datetime(2024, 6, 3, tzinfo=timezone.utc),
        trade_side=PaperTradeSide.LONG,
        quantity=qty,
        entry_price=100.0,
        exit_price=100.0 + pnl / qty,
        stop_loss=98.0,
        exit_reason=PaperExitReason.EOD_EXIT,
        slippage=0.0,
        brokerage=40.0,
        pnl=pnl,
        pnl_percent=0.0,
        opened_at=datetime(2024, 6, 3, 4, 0, 0, tzinfo=timezone.utc),
        closed_at=now,
        metadata={},
        created_at=now,
    )


def _account(starting: float = 100_000.0, available: float | None = None) -> PaperAccount:
    return PaperAccount.model_construct(
        account_id="default",
        starting_capital=starting,
        available_capital=available if available is not None else starting,
        used_capital=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        consecutive_losses=0,
        is_paused=False,
        pause_reason=None,
        last_reset_date=None,
        updated_at=datetime(2024, 6, 3, tzinfo=timezone.utc),
    )


# ── Tests ────────────────────────────────────────────────────────────────────

class TestUnrealized:
    def test_long_unrealized_positive_on_price_rise(self) -> None:
        pnl = calculate_unrealized_pnl(PaperTradeSide.LONG, 10, entry_price=100.0, current_price=105.0)
        assert pnl == pytest.approx(50.0)

    def test_long_unrealized_negative_on_price_drop(self) -> None:
        pnl = calculate_unrealized_pnl(PaperTradeSide.LONG, 10, entry_price=100.0, current_price=98.0)
        assert pnl == pytest.approx(-20.0)

    def test_short_unrealized_positive_on_price_drop(self) -> None:
        pnl = calculate_unrealized_pnl(PaperTradeSide.SHORT, 10, entry_price=100.0, current_price=95.0)
        assert pnl == pytest.approx(50.0)

    def test_short_unrealized_negative_on_price_rise(self) -> None:
        pnl = calculate_unrealized_pnl(PaperTradeSide.SHORT, 10, entry_price=100.0, current_price=102.0)
        assert pnl == pytest.approx(-20.0)


class TestRealized:
    def test_long_realized_subtracts_brokerage(self) -> None:
        pnl = calculate_realized_pnl(
            PaperTradeSide.LONG, 10, entry_price=100.0, exit_price=110.0, brokerage_total=40.0
        )
        # gross = 100, net = 60
        assert pnl == pytest.approx(60.0)

    def test_short_realized_subtracts_brokerage(self) -> None:
        pnl = calculate_realized_pnl(
            PaperTradeSide.SHORT, 10, entry_price=100.0, exit_price=90.0, brokerage_total=40.0
        )
        assert pnl == pytest.approx(60.0)


class TestPercent:
    def test_pnl_percent_basic(self) -> None:
        assert calculate_pnl_percent(50.0, 1000.0) == pytest.approx(5.0)

    def test_pnl_percent_zero_capital_safe(self) -> None:
        assert calculate_pnl_percent(50.0, 0.0) == 0.0


class TestAggregate:
    def test_aggregate_combines_open_and_closed(self) -> None:
        p = _position(side=PaperTradeSide.LONG, entry=100, current=105, qty=10)
        p.unrealized_pnl = 50.0
        t = _trade(pnl=200.0)
        agg = aggregate_pnl([p], [t])
        assert agg.realized_pnl == pytest.approx(200.0)
        assert agg.unrealized_pnl == pytest.approx(50.0)
        assert agg.total_pnl == pytest.approx(250.0)


class TestEquityCurve:
    def test_equity_curve_increments_chronologically(self) -> None:
        t1 = _trade(pnl=100.0)
        t2 = _trade(pnl=-50.0)
        t2.closed_at = datetime(2024, 6, 3, 9, 0, 0, tzinfo=timezone.utc)
        curve = equity_curve_from_trades(starting_capital=10_000.0, trades=[t1, t2])
        assert len(curve) == 2
        assert curve[0].equity == pytest.approx(10_100.0)
        assert curve[1].equity == pytest.approx(10_050.0)
        assert curve[1].cumulative_pnl == pytest.approx(50.0)

    def test_equity_curve_empty_on_no_trades(self) -> None:
        assert equity_curve_from_trades(10_000.0, []) == []


class TestAccountHelpers:
    def test_apply_entry_locks_capital(self) -> None:
        acc = _account(starting=100_000.0)
        apply_entry_to_account(acc, capital_used=10_000.0)
        assert acc.available_capital == pytest.approx(90_000.0)
        assert acc.used_capital == pytest.approx(10_000.0)

    def test_apply_realized_winning_resets_consecutive_losses(self) -> None:
        acc = _account()
        acc.consecutive_losses = 2
        # Trade default uses qty=10 entry=100 → 1_000 deployed.
        acc.available_capital = 99_000.0
        acc.used_capital = 1_000.0
        t = _trade(pnl=200.0)
        apply_realized_pnl_to_account(acc, t)
        assert acc.consecutive_losses == 0
        assert acc.winning_trades == 1
        assert acc.total_trades == 1
        assert acc.realized_pnl == pytest.approx(200.0)
        # Capital restored: prior available + entry capital + pnl returned.
        assert acc.available_capital == pytest.approx(99_000.0 + 1_000.0 + 200.0)
        assert acc.used_capital == pytest.approx(0.0)

    def test_apply_realized_losing_increments_consecutive_losses(self) -> None:
        acc = _account()
        acc.consecutive_losses = 1
        acc.available_capital = 90_000.0
        acc.used_capital = 10_000.0
        t = _trade(pnl=-150.0)
        apply_realized_pnl_to_account(acc, t)
        assert acc.consecutive_losses == 2
        assert acc.losing_trades == 1
        assert acc.realized_pnl == pytest.approx(-150.0)

    def test_refresh_unrealized_aggregates_open(self) -> None:
        acc = _account()
        p1 = _position(entry=100, current=105, qty=10)
        p1.unrealized_pnl = 50.0
        p2 = _position(entry=200, current=195, qty=5)
        p2.unrealized_pnl = -25.0
        refresh_unrealized_on_account(acc, [p1, p2])
        assert acc.unrealized_pnl == pytest.approx(25.0)


class TestROI:
    def test_roi_percent(self) -> None:
        assert roi_percent(100_000.0, 5_000.0) == pytest.approx(5.0)

    def test_roi_zero_capital_safe(self) -> None:
        assert roi_percent(0.0, 100.0) == 0.0
