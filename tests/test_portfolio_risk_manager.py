"""
Unit tests for the portfolio risk manager.

All pure logic — stateless, no MongoDB, no async.

Tests verify:
  - Each rule rejects exactly when expected.
  - All rules pass on a healthy portfolio context.
  - Combined exposure rules interact correctly.
"""

from __future__ import annotations

import pytest

from app.portfolio.portfolio_risk_manager import (
    PortfolioRiskCheckResult,
    PortfolioRiskContext,
    PortfolioRiskManager,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _rm(**kwargs) -> PortfolioRiskManager:
    defaults = dict(
        max_open_positions=5,
        max_capital_exposure_pct=0.80,
        max_daily_loss_pct=0.02,
        max_capital_per_trade_pct=0.20,
        max_capital_per_strategy_pct=0.50,
        max_capital_per_sector_pct=0.40,
        max_correlated_positions=3,
    )
    defaults.update(kwargs)
    return PortfolioRiskManager(**defaults)


def _ctx(**kwargs) -> PortfolioRiskContext:
    defaults = dict(
        symbol="RELIANCE",
        strategy_id="one_side_orb",
        sector="Energy",
        total_capital=1_000_000.0,
        available_capital=800_000.0,
        proposed_allocation=100_000.0,
        used_capital=0.0,
        open_positions=0,
        strategy_used_capital=0.0,
        sector_used_capital=0.0,
        correlated_positions=0,
    )
    defaults.update(kwargs)
    return PortfolioRiskContext(**defaults)


# ── Happy path ────────────────────────────────────────────────────────────────

def test_all_rules_pass_on_healthy_context():
    rm = _rm()
    result = rm.evaluate(_ctx())
    assert result.accepted is True
    assert result.reason is None


# ── Rule 1: halt gate ─────────────────────────────────────────────────────────

def test_halt_gate_blocks_when_halted():
    rm = _rm()
    result = rm.evaluate(_ctx(), daily_loss=0.0, is_halted=True)
    assert result.accepted is False
    assert result.reason == "portfolio_halted"


def test_halt_gate_blocks_when_daily_loss_exceeds_limit():
    rm = _rm(max_daily_loss_pct=0.02)
    result = rm.evaluate(_ctx(), daily_loss=-20_001.0)   # > 2% of 1M
    assert result.accepted is False
    assert result.reason == "daily_loss_limit_breached"


def test_halt_gate_allows_loss_below_limit():
    rm = _rm(max_daily_loss_pct=0.02)
    result = rm.evaluate(_ctx(), daily_loss=-15_000.0)   # 1.5% < 2%
    assert result.accepted is True


def test_halt_gate_allows_zero_loss():
    rm = _rm()
    result = rm.evaluate(_ctx(), daily_loss=0.0)
    assert result.accepted is True


# ── Rule 2: max open positions ────────────────────────────────────────────────

def test_max_open_positions_blocked_at_cap():
    rm = _rm(max_open_positions=5)
    result = rm.evaluate(_ctx(open_positions=5))
    assert result.accepted is False
    assert result.reason == "max_open_positions_reached"


def test_max_open_positions_allowed_below_cap():
    rm = _rm(max_open_positions=5)
    result = rm.evaluate(_ctx(open_positions=4))
    assert result.accepted is True


# ── Rule 3: max capital exposure ──────────────────────────────────────────────

def test_max_exposure_blocked_when_exceeded():
    rm = _rm(max_capital_exposure_pct=0.80)
    # 750k used + 100k proposed = 850k / 1M = 85% > 80%
    result = rm.evaluate(_ctx(used_capital=750_000.0, proposed_allocation=100_000.0))
    assert result.accepted is False
    assert result.reason == "max_capital_exposure_exceeded"


def test_max_exposure_allowed_when_within_limit():
    rm = _rm(max_capital_exposure_pct=0.80)
    # 600k used + 100k proposed = 700k / 1M = 70% ≤ 80%
    result = rm.evaluate(_ctx(used_capital=600_000.0, proposed_allocation=100_000.0))
    assert result.accepted is True


# ── Rule 4: max capital per trade ─────────────────────────────────────────────

def test_max_per_trade_blocked_when_exceeded():
    rm = _rm(max_capital_per_trade_pct=0.20)
    # 20% of 1M = 200k; proposing 250k → blocked
    result = rm.evaluate(_ctx(proposed_allocation=250_000.0))
    assert result.accepted is False
    assert result.reason == "max_capital_per_trade_exceeded"


def test_max_per_trade_allowed_at_exact_limit():
    rm = _rm(max_capital_per_trade_pct=0.20)
    result = rm.evaluate(_ctx(proposed_allocation=200_000.0))
    assert result.accepted is True


# ── Rule 5: max capital per strategy ─────────────────────────────────────────

def test_max_per_strategy_blocked_when_exceeded():
    rm = _rm(max_capital_per_strategy_pct=0.50)
    # 450k already + 100k proposed = 550k / 1M = 55% > 50% → blocked
    result = rm.evaluate(_ctx(strategy_used_capital=450_000.0, proposed_allocation=100_000.0))
    assert result.accepted is False
    assert result.reason == "max_capital_per_strategy_exceeded"


def test_max_per_strategy_allowed_within_limit():
    rm = _rm(max_capital_per_strategy_pct=0.50)
    # 300k + 100k = 400k / 1M = 40% ≤ 50%
    result = rm.evaluate(_ctx(strategy_used_capital=300_000.0, proposed_allocation=100_000.0))
    assert result.accepted is True


# ── Rule 6: max capital per sector ───────────────────────────────────────────

def test_max_per_sector_blocked_when_exceeded():
    rm = _rm(max_capital_per_sector_pct=0.40)
    # 350k + 100k = 450k / 1M = 45% > 40% → blocked
    result = rm.evaluate(_ctx(sector_used_capital=350_000.0, sector="Energy"))
    assert result.accepted is False
    assert result.reason == "max_capital_per_sector_exceeded"


def test_max_per_sector_skipped_when_sector_is_none():
    rm = _rm(max_capital_per_sector_pct=0.10)
    # Sector=None should bypass the sector rule entirely
    result = rm.evaluate(_ctx(sector=None, sector_used_capital=900_000.0))
    assert result.accepted is True


# ── Rule 7: max correlated positions ─────────────────────────────────────────

def test_correlated_positions_blocked_at_cap():
    rm = _rm(max_correlated_positions=3)
    result = rm.evaluate(_ctx(correlated_positions=3, sector="Energy"))
    assert result.accepted is False
    assert result.reason == "max_correlated_positions_reached"


def test_correlated_positions_allowed_below_cap():
    rm = _rm(max_correlated_positions=3)
    result = rm.evaluate(_ctx(correlated_positions=2, sector="Energy"))
    assert result.accepted is True


def test_correlated_positions_skipped_when_sector_is_none():
    rm = _rm(max_correlated_positions=1)
    result = rm.evaluate(_ctx(sector=None, correlated_positions=99))
    assert result.accepted is True


# ── Rule 8: available capital ─────────────────────────────────────────────────

def test_insufficient_available_capital_blocked():
    rm = _rm()
    result = rm.evaluate(_ctx(available_capital=50_000.0, proposed_allocation=100_000.0))
    assert result.accepted is False
    assert result.reason == "insufficient_available_capital"


def test_exactly_sufficient_capital_allowed():
    rm = _rm()
    result = rm.evaluate(_ctx(available_capital=100_000.0, proposed_allocation=100_000.0))
    assert result.accepted is True


# ── Rule priority: halt fires before open-positions ──────────────────────────

def test_halt_fires_before_open_positions():
    rm = _rm(max_open_positions=0)   # open positions would also fire
    result = rm.evaluate(_ctx(), is_halted=True)
    assert result.reason == "portfolio_halted"
