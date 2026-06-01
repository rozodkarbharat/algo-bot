"""
Risk monitor — validates portfolio risk limits in real-time.

Checks:
  - Daily loss vs configured limit
  - Total capital exposure vs max exposure
  - Per-strategy concentration vs max strategy %
  - Per-sector concentration vs max sector %
  - Open position count vs max open positions

Routes alerts via `alert_router` when any limit is approached (80% of max)
or breached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.config.settings import settings
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, now_utc
from app.utils.trading_day import today_ist

logger = get_logger(__name__)

WARN_FRACTION = 0.80  # warn when 80% of any limit is reached


@dataclass
class RiskLimitCheck:
    name: str
    current: float
    limit: float
    breached: bool
    warning: bool
    pct_used: float


@dataclass
class RiskHealthReport:
    """Portfolio risk snapshot for monitoring."""

    total_capital: float
    used_capital: float
    available_capital: float
    daily_pnl: float
    open_positions: int
    is_halted: bool
    halt_reason: Optional[str]

    limit_checks: list[RiskLimitCheck] = field(default_factory=list)
    strategy_exposure: dict = field(default_factory=dict)
    sector_exposure: dict = field(default_factory=dict)

    any_breached: bool = False
    any_warning: bool = False
    notes: list[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=now_utc)


class RiskMonitor:
    """
    Monitors portfolio-level risk limits and fires alerts when breached.
    """

    async def check(self) -> RiskHealthReport:
        """Build and return the current risk health snapshot."""
        try:
            from app.repositories.portfolio_risk_state_repository import (
                PortfolioRiskStateRepository,
            )
            from app.monitoring.alert_router import alert_router

            today_dt = date_to_utc_midnight(today_ist())
            repo = PortfolioRiskStateRepository()
            state = await repo.get_for_date(today_dt)

            total_cap = settings.PORTFOLIO_TOTAL_CAPITAL

            if state is None:
                return RiskHealthReport(
                    total_capital=total_cap,
                    used_capital=0.0,
                    available_capital=total_cap,
                    daily_pnl=0.0,
                    open_positions=0,
                    is_halted=False,
                    halt_reason=None,
                    notes=["No portfolio risk state yet today."],
                )

            checks: list[RiskLimitCheck] = []
            notes: list[str] = []

            # ── Daily loss check ───────────────────────────────────────────────
            daily_loss_limit = total_cap * settings.PORTFOLIO_MAX_DAILY_LOSS_PCT / 100.0
            daily_loss = min(state.realized_pnl_today, 0.0)
            loss_abs = abs(daily_loss)
            loss_pct = loss_abs / daily_loss_limit if daily_loss_limit else 0.0
            loss_check = RiskLimitCheck(
                name="daily_loss",
                current=loss_abs,
                limit=daily_loss_limit,
                breached=state.is_halted,
                warning=loss_pct >= WARN_FRACTION and not state.is_halted,
                pct_used=round(loss_pct * 100, 2),
            )
            checks.append(loss_check)
            if loss_check.breached:
                notes.append(f"Daily loss limit breached: ₹{loss_abs:,.2f} of ₹{daily_loss_limit:,.2f}")
                await alert_router.daily_loss_limit_breached(daily_loss, daily_loss_limit)

            # ── Capital exposure check ─────────────────────────────────────────
            max_exposure = total_cap * settings.PORTFOLIO_MAX_CAPITAL_EXPOSURE_PCT / 100.0
            exp_pct = state.used_capital / max_exposure if max_exposure else 0.0
            exp_check = RiskLimitCheck(
                name="capital_exposure",
                current=state.used_capital,
                limit=max_exposure,
                breached=state.used_capital > max_exposure,
                warning=exp_pct >= WARN_FRACTION and state.used_capital <= max_exposure,
                pct_used=round(exp_pct * 100, 2),
            )
            checks.append(exp_check)
            if exp_check.warning or exp_check.breached:
                util = round(state.used_capital / total_cap * 100, 1)
                await alert_router.exposure_limit_warning(util, settings.PORTFOLIO_MAX_CAPITAL_EXPOSURE_PCT)
                notes.append(f"Capital exposure high: {util:.1f}%")

            # ── Per-strategy concentration ──────────────────────────────────────
            max_strat_cap = total_cap * settings.PORTFOLIO_MAX_CAPITAL_PER_STRATEGY_PCT / 100.0
            for strat_id, strat_cap in (state.strategy_exposure or {}).items():
                pct = strat_cap / max_strat_cap if max_strat_cap else 0.0
                if pct >= WARN_FRACTION:
                    strat_pct = round(strat_cap / total_cap * 100, 1)
                    await alert_router.strategy_concentration_warning(
                        strat_id, strat_pct, settings.PORTFOLIO_MAX_CAPITAL_PER_STRATEGY_PCT
                    )
                    notes.append(f"Strategy {strat_id} concentration: {strat_pct:.1f}%")

            # ── Per-sector concentration ───────────────────────────────────────
            max_sector_cap = total_cap * settings.PORTFOLIO_MAX_CAPITAL_PER_SECTOR_PCT / 100.0
            for sector, sec_cap in (state.sector_exposure or {}).items():
                pct = sec_cap / max_sector_cap if max_sector_cap else 0.0
                if pct >= WARN_FRACTION:
                    sec_pct = round(sec_cap / total_cap * 100, 1)
                    await alert_router.sector_concentration_warning(
                        sector, sec_pct, settings.PORTFOLIO_MAX_CAPITAL_PER_SECTOR_PCT
                    )
                    notes.append(f"Sector {sector} concentration: {sec_pct:.1f}%")

            any_breached = any(c.breached for c in checks)
            any_warning = any(c.warning for c in checks)

            return RiskHealthReport(
                total_capital=state.total_capital,
                used_capital=state.used_capital,
                available_capital=state.available_capital,
                daily_pnl=state.realized_pnl_today,
                open_positions=state.open_positions,
                is_halted=state.is_halted,
                halt_reason=state.halt_reason,
                limit_checks=checks,
                strategy_exposure=state.strategy_exposure or {},
                sector_exposure=state.sector_exposure or {},
                any_breached=any_breached,
                any_warning=any_warning,
                notes=notes,
            )

        except Exception as exc:
            logger.error("[risk-monitor] check failed: %s", exc)
            return RiskHealthReport(
                total_capital=settings.PORTFOLIO_TOTAL_CAPITAL,
                used_capital=0.0,
                available_capital=settings.PORTFOLIO_TOTAL_CAPITAL,
                daily_pnl=0.0,
                open_positions=0,
                is_halted=False,
                halt_reason=None,
                notes=[f"Risk monitor failed: {exc}"],
            )


risk_monitor = RiskMonitor()
