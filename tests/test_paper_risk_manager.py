"""
Unit tests for the paper trading risk manager.

Pure logic — verifies each rule rejects exactly when expected and that
all rules pass on a healthy account.
"""

from __future__ import annotations

import pytest

from app.models.paper_account import PaperAccount
from app.paper_trading.risk_manager import PaperRiskManager, RiskContext


def _account(
    starting: float = 100_000.0,
    available: float | None = None,
    used: float = 0.0,
    daily_pnl: float = 0.0,
    unrealized: float = 0.0,
    consecutive_losses: int = 0,
    is_paused: bool = False,
    pause_reason: str | None = None,
) -> PaperAccount:
    from datetime import datetime, timezone
    return PaperAccount.model_construct(
        account_id="default",
        starting_capital=starting,
        available_capital=available if available is not None else starting,
        used_capital=used,
        realized_pnl=0.0,
        unrealized_pnl=unrealized,
        daily_pnl=daily_pnl,
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        consecutive_losses=consecutive_losses,
        is_paused=is_paused,
        pause_reason=pause_reason,
        last_reset_date=None,
        updated_at=datetime(2024, 6, 3, tzinfo=timezone.utc),
    )


def _ctx(
    capital_required: float = 10_000.0,
    open_position_count: int = 0,
    has_open_for_symbol: bool = False,
    trades_taken_today: int = 0,
) -> RiskContext:
    return RiskContext(
        symbol="RELIANCE",
        capital_required=capital_required,
        open_position_count=open_position_count,
        has_open_for_symbol=has_open_for_symbol,
        trades_taken_today=trades_taken_today,
    )


class TestRiskAccept:
    def test_clean_account_accepts(self) -> None:
        rm = PaperRiskManager(
            max_open_positions=5,
            max_trades_per_day=10,
            max_daily_loss_pct=2.0,
            consecutive_loss_cooldown=3,
            max_position_pct=20.0,
        )
        result = rm.evaluate(_account(), _ctx())
        assert result.accepted is True


class TestRiskReject:
    def test_paused_account_rejects(self) -> None:
        rm = PaperRiskManager()
        acc = _account(is_paused=True, pause_reason="manual_pause")
        result = rm.evaluate(acc, _ctx())
        assert result.accepted is False
        assert result.reason == "paper_trading_paused"

    def test_duplicate_symbol_rejects(self) -> None:
        rm = PaperRiskManager()
        result = rm.evaluate(_account(), _ctx(has_open_for_symbol=True))
        assert result.accepted is False
        assert result.reason == "duplicate_position_for_symbol_today"

    def test_max_open_rejects(self) -> None:
        rm = PaperRiskManager(max_open_positions=2)
        result = rm.evaluate(_account(), _ctx(open_position_count=2))
        assert result.accepted is False
        assert result.reason == "max_open_positions_exceeded"

    def test_max_trades_per_day_rejects(self) -> None:
        rm = PaperRiskManager(max_trades_per_day=3)
        result = rm.evaluate(_account(), _ctx(trades_taken_today=3))
        assert result.accepted is False
        assert result.reason == "max_daily_trades_exceeded"

    def test_position_size_rejects_when_too_large(self) -> None:
        rm = PaperRiskManager(max_position_pct=5.0)
        # 6_000 > 5% of 100_000 = 5_000
        result = rm.evaluate(_account(), _ctx(capital_required=6_000.0))
        assert result.accepted is False
        assert result.reason == "position_size_exceeds_cap"

    def test_insufficient_capital_rejects(self) -> None:
        rm = PaperRiskManager()
        acc = _account(available=500.0)
        result = rm.evaluate(acc, _ctx(capital_required=1_000.0))
        assert result.accepted is False
        assert result.reason == "insufficient_available_capital"

    def test_daily_loss_threshold_rejects(self) -> None:
        rm = PaperRiskManager(max_daily_loss_pct=1.0)
        # 1% of 100_000 = -1_000 threshold; daily + unrealized = -1_500
        acc = _account(daily_pnl=-1_200.0, unrealized=-300.0)
        result = rm.evaluate(acc, _ctx())
        assert result.accepted is False
        assert result.reason == "daily_loss_limit_breached"

    def test_consecutive_losses_rejects(self) -> None:
        rm = PaperRiskManager(consecutive_loss_cooldown=3)
        acc = _account(consecutive_losses=3)
        result = rm.evaluate(acc, _ctx())
        assert result.accepted is False
        assert result.reason == "consecutive_loss_cooldown"


class TestAutoPauseHelpers:
    def test_should_pause_for_daily_loss(self) -> None:
        rm = PaperRiskManager(max_daily_loss_pct=2.0)
        acc = _account(daily_pnl=-1_500.0, unrealized=-600.0)
        assert rm.should_pause_for_daily_loss(acc) is True

    def test_should_pause_for_consecutive_losses(self) -> None:
        rm = PaperRiskManager(consecutive_loss_cooldown=3)
        acc = _account(consecutive_losses=3)
        assert rm.should_pause_for_consecutive_losses(acc) is True
