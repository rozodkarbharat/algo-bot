"""
Unit tests for the live risk manager.

Verifies each rule rejects exactly when expected and that a clean
context passes through.
"""

from __future__ import annotations

import pytest

from app.live_execution.live_risk_manager import (
    LiveRiskContext,
    LiveRiskManager,
)


def _ctx(
    symbol: str = "RELIANCE",
    capital_required: float = 10_000.0,
    open_position_count: int = 0,
    has_open_for_symbol: bool = False,
    trades_taken_today: int = 0,
    current_exposure: float = 0.0,
    realized_pnl_today: float = 0.0,
    unrealized_pnl: float = 0.0,
    peak_equity: float = 500_000.0,
    current_equity: float = 500_000.0,
    kill_switch_engaged: bool = False,
    is_account_paused: bool = False,
    broker_session_healthy: bool = True,
) -> LiveRiskContext:
    return LiveRiskContext(
        symbol=symbol,
        capital_required=capital_required,
        open_position_count=open_position_count,
        has_open_for_symbol=has_open_for_symbol,
        trades_taken_today=trades_taken_today,
        current_exposure=current_exposure,
        realized_pnl_today=realized_pnl_today,
        unrealized_pnl=unrealized_pnl,
        peak_equity=peak_equity,
        current_equity=current_equity,
        kill_switch_engaged=kill_switch_engaged,
        is_account_paused=is_account_paused,
        broker_session_healthy=broker_session_healthy,
    )


def _rm(**overrides) -> LiveRiskManager:
    return LiveRiskManager(
        total_capital=overrides.get("total_capital", 500_000.0),
        max_open_positions=overrides.get("max_open_positions", 3),
        max_trades_per_day=overrides.get("max_trades_per_day", 5),
        max_position_pct=overrides.get("max_position_pct", 15.0),
        max_exposure_pct=overrides.get("max_exposure_pct", 50.0),
        max_daily_loss_pct=overrides.get("max_daily_loss_pct", 1.5),
        max_drawdown_pct=overrides.get("max_drawdown_pct", 5.0),
    )


class TestLiveRiskAccept:
    def test_clean_context_accepts(self) -> None:
        result = _rm().evaluate(_ctx())
        assert result.accepted is True


class TestLiveRiskReject:
    def test_kill_switch_rejects(self) -> None:
        result = _rm().evaluate(_ctx(kill_switch_engaged=True))
        assert result.accepted is False
        assert result.reason == "kill_switch_engaged"

    def test_paused_account_rejects(self) -> None:
        result = _rm().evaluate(_ctx(is_account_paused=True))
        assert result.accepted is False
        assert result.reason == "live_trading_paused"

    def test_unhealthy_broker_rejects(self) -> None:
        result = _rm().evaluate(_ctx(broker_session_healthy=False))
        assert result.accepted is False
        assert result.reason == "broker_session_unhealthy"

    def test_duplicate_symbol_rejects(self) -> None:
        result = _rm().evaluate(_ctx(has_open_for_symbol=True))
        assert result.accepted is False
        assert result.reason == "duplicate_position_for_symbol_today"

    def test_max_open_rejects(self) -> None:
        result = _rm(max_open_positions=2).evaluate(_ctx(open_position_count=2))
        assert result.accepted is False
        assert result.reason == "max_open_positions_exceeded"

    def test_max_trades_rejects(self) -> None:
        result = _rm(max_trades_per_day=2).evaluate(_ctx(trades_taken_today=2))
        assert result.accepted is False
        assert result.reason == "max_daily_trades_exceeded"

    def test_position_size_rejects(self) -> None:
        # 15% of 500k = 75_000. 80_000 > 75_000.
        result = _rm().evaluate(_ctx(capital_required=80_000.0))
        assert result.accepted is False
        assert result.reason == "position_size_exceeds_cap"

    def test_aggregate_exposure_rejects(self) -> None:
        # 50% of 500k = 250_000. Existing 200k + new 60k = 260k.
        result = _rm().evaluate(
            _ctx(current_exposure=200_000.0, capital_required=60_000.0)
        )
        assert result.accepted is False
        assert result.reason == "max_capital_exposure_exceeded"

    def test_daily_loss_rejects(self) -> None:
        # 1.5% of 500k = 7_500 loss threshold. realized -5_000 + unrealized -3_000 = -8_000.
        result = _rm().evaluate(
            _ctx(realized_pnl_today=-5_000.0, unrealized_pnl=-3_000.0)
        )
        assert result.accepted is False
        assert result.reason == "daily_loss_limit_breached"

    def test_drawdown_rejects(self) -> None:
        # peak 500k → current 470k → 6% drawdown > 5%
        result = _rm().evaluate(_ctx(peak_equity=500_000.0, current_equity=470_000.0))
        assert result.accepted is False
        assert result.reason == "max_drawdown_breached"


class TestLiveRiskHaltHelpers:
    def test_should_halt_for_daily_loss(self) -> None:
        rm = _rm(max_daily_loss_pct=1.0)
        ctx = _ctx(realized_pnl_today=-3_000.0, unrealized_pnl=-3_000.0)
        assert rm.should_halt_for_daily_loss(ctx) is True

    def test_should_halt_for_drawdown(self) -> None:
        rm = _rm(max_drawdown_pct=2.0)
        ctx = _ctx(peak_equity=500_000.0, current_equity=485_000.0)
        assert rm.should_halt_for_drawdown(ctx) is True

    def test_should_not_halt_for_drawdown_when_peak_zero(self) -> None:
        rm = _rm()
        ctx = _ctx(peak_equity=0.0, current_equity=-100.0)
        assert rm.should_halt_for_drawdown(ctx) is False
