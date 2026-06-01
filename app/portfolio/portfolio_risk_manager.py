"""
Portfolio-level risk manager — pre-allocation gatekeeper.

Evaluates each candidate signal against a set of portfolio-wide rules
before the capital allocator is allowed to size the position. This sits
ABOVE strategy-level risk (the strategy's own stop-loss / range filters
already ran during signal generation); this layer enforces portfolio
composition constraints.

Rules implemented:
  1. Portfolio halt gate — daily loss limit has not been breached.
  2. Max open positions cap.
  3. Max capital exposure — used_capital / total_capital ≤ threshold.
  4. Max capital per trade — allocated ≤ max_per_trade.
  5. Max capital per strategy — strategy already at its cap.
  6. Max capital per sector — sector already at its cap.
  7. Max correlated positions — too many open positions in the same sector.
  8. Sufficient available capital — at least the allocated amount is free.

This class is stateless; all portfolio state is passed in via
`PortfolioRiskContext`. The service layer fetches the `PortfolioRiskState`
document and builds the context before calling `evaluate()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Input / output types ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class PortfolioRiskContext:
    """
    Snapshot the service must supply for each risk evaluation.

    Keeping context explicit (not reading from DB inside the risk manager)
    preserves testability and ensures consistency within a single allocation
    batch — the service can compute the context once and pass it to every
    candidate in the batch.
    """

    symbol: str
    strategy_id: str
    sector: Optional[str]

    # Capital inputs
    total_capital: float
    available_capital: float
    proposed_allocation: float      # ₹ the allocator wants to deploy

    # Current portfolio state
    used_capital: float             # already committed capital
    open_positions: int             # currently active allocations
    strategy_used_capital: float    # capital already in this strategy
    sector_used_capital: float      # capital already in this sector
    correlated_positions: int       # open positions in the same sector


@dataclass(frozen=True)
class PortfolioRiskCheckResult:
    """Result of a single portfolio risk evaluation."""

    accepted: bool
    reason: Optional[str] = None
    detail: Optional[dict] = None


# ── Risk manager ──────────────────────────────────────────────────────────────

class PortfolioRiskManager:
    """
    Stateless portfolio-level risk gatekeeper.

    All thresholds default to the values in `settings`. Override per-instance
    for tests or alternate capital configurations.
    """

    def __init__(
        self,
        max_open_positions: int = 10,
        max_capital_exposure_pct: float = 0.80,
        max_daily_loss_pct: float = 0.02,
        max_capital_per_trade_pct: float = 0.20,
        max_capital_per_strategy_pct: float = 0.50,
        max_capital_per_sector_pct: float = 0.40,
        max_correlated_positions: int = 3,
    ) -> None:
        self._max_open = max_open_positions
        self._max_exposure_pct = max_capital_exposure_pct
        self._max_daily_loss_pct = max_daily_loss_pct
        self._max_per_trade_pct = max_capital_per_trade_pct
        self._max_per_strategy_pct = max_capital_per_strategy_pct
        self._max_per_sector_pct = max_capital_per_sector_pct
        self._max_correlated = max_correlated_positions

    # ── Public API ────────────────────────────────────────────────────────────

    def evaluate(
        self,
        ctx: PortfolioRiskContext,
        *,
        daily_loss: float = 0.0,
        is_halted: bool = False,
    ) -> PortfolioRiskCheckResult:
        """
        Run all risk rules against the supplied context.

        Evaluates rules in priority order; first failure short-circuits.

        Parameters
        ----------
        ctx : PortfolioRiskContext
            Snapshot of portfolio state for this candidate.
        daily_loss : float
            Today's total realised loss (negative ₹). Positive → no loss yet.
        is_halted : bool
            True when the portfolio has already tripped the daily-loss halt.
        """
        for check in self._rules():
            result = check(ctx, daily_loss, is_halted)
            if not result.accepted:
                logger.info(
                    "[portfolio-risk] rejected %s/%s: %s",
                    ctx.strategy_id, ctx.symbol, result.reason,
                )
                return result
        return PortfolioRiskCheckResult(accepted=True)

    # ── Rule definitions ──────────────────────────────────────────────────────

    def _rules(self):
        return [
            self._check_halt,
            self._check_max_open_positions,
            self._check_max_exposure,
            self._check_max_per_trade,
            self._check_max_per_strategy,
            self._check_max_per_sector,
            self._check_correlated_positions,
            self._check_available_capital,
        ]

    def _check_halt(
        self, ctx: PortfolioRiskContext, daily_loss: float, is_halted: bool
    ) -> PortfolioRiskCheckResult:
        if is_halted:
            return PortfolioRiskCheckResult(
                accepted=False,
                reason="portfolio_halted",
                detail={"daily_loss": daily_loss},
            )
        loss_pct = abs(daily_loss) / ctx.total_capital if ctx.total_capital > 0 else 0.0
        if daily_loss < 0 and loss_pct >= self._max_daily_loss_pct:
            return PortfolioRiskCheckResult(
                accepted=False,
                reason="daily_loss_limit_breached",
                detail={
                    "daily_loss": daily_loss,
                    "loss_pct": round(loss_pct, 4),
                    "limit_pct": self._max_daily_loss_pct,
                },
            )
        return PortfolioRiskCheckResult(accepted=True)

    def _check_max_open_positions(
        self, ctx: PortfolioRiskContext, *_
    ) -> PortfolioRiskCheckResult:
        if ctx.open_positions >= self._max_open:
            return PortfolioRiskCheckResult(
                accepted=False,
                reason="max_open_positions_reached",
                detail={"open": ctx.open_positions, "limit": self._max_open},
            )
        return PortfolioRiskCheckResult(accepted=True)

    def _check_max_exposure(
        self, ctx: PortfolioRiskContext, *_
    ) -> PortfolioRiskCheckResult:
        projected = ctx.used_capital + ctx.proposed_allocation
        exposure_pct = projected / ctx.total_capital if ctx.total_capital > 0 else 0.0
        if exposure_pct > self._max_exposure_pct:
            return PortfolioRiskCheckResult(
                accepted=False,
                reason="max_capital_exposure_exceeded",
                detail={
                    "projected_exposure_pct": round(exposure_pct, 4),
                    "limit_pct": self._max_exposure_pct,
                },
            )
        return PortfolioRiskCheckResult(accepted=True)

    def _check_max_per_trade(
        self, ctx: PortfolioRiskContext, *_
    ) -> PortfolioRiskCheckResult:
        limit = ctx.total_capital * self._max_per_trade_pct
        if ctx.proposed_allocation > limit:
            return PortfolioRiskCheckResult(
                accepted=False,
                reason="max_capital_per_trade_exceeded",
                detail={
                    "proposed": ctx.proposed_allocation,
                    "limit": round(limit, 2),
                },
            )
        return PortfolioRiskCheckResult(accepted=True)

    def _check_max_per_strategy(
        self, ctx: PortfolioRiskContext, *_
    ) -> PortfolioRiskCheckResult:
        limit = ctx.total_capital * self._max_per_strategy_pct
        projected = ctx.strategy_used_capital + ctx.proposed_allocation
        if projected > limit:
            return PortfolioRiskCheckResult(
                accepted=False,
                reason="max_capital_per_strategy_exceeded",
                detail={
                    "strategy_id": ctx.strategy_id,
                    "projected": round(projected, 2),
                    "limit": round(limit, 2),
                },
            )
        return PortfolioRiskCheckResult(accepted=True)

    def _check_max_per_sector(
        self, ctx: PortfolioRiskContext, *_
    ) -> PortfolioRiskCheckResult:
        if not ctx.sector:
            return PortfolioRiskCheckResult(accepted=True)
        limit = ctx.total_capital * self._max_per_sector_pct
        projected = ctx.sector_used_capital + ctx.proposed_allocation
        if projected > limit:
            return PortfolioRiskCheckResult(
                accepted=False,
                reason="max_capital_per_sector_exceeded",
                detail={
                    "sector": ctx.sector,
                    "projected": round(projected, 2),
                    "limit": round(limit, 2),
                },
            )
        return PortfolioRiskCheckResult(accepted=True)

    def _check_correlated_positions(
        self, ctx: PortfolioRiskContext, *_
    ) -> PortfolioRiskCheckResult:
        if not ctx.sector:
            return PortfolioRiskCheckResult(accepted=True)
        if ctx.correlated_positions >= self._max_correlated:
            return PortfolioRiskCheckResult(
                accepted=False,
                reason="max_correlated_positions_reached",
                detail={
                    "sector": ctx.sector,
                    "correlated": ctx.correlated_positions,
                    "limit": self._max_correlated,
                },
            )
        return PortfolioRiskCheckResult(accepted=True)

    def _check_available_capital(
        self, ctx: PortfolioRiskContext, *_
    ) -> PortfolioRiskCheckResult:
        if ctx.available_capital < ctx.proposed_allocation:
            return PortfolioRiskCheckResult(
                accepted=False,
                reason="insufficient_available_capital",
                detail={
                    "available": round(ctx.available_capital, 2),
                    "required": round(ctx.proposed_allocation, 2),
                },
            )
        return PortfolioRiskCheckResult(accepted=True)
