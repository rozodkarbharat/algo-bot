"""
Live risk manager — pre-trade gatekeeper for REAL-money orders.

Pattern mirrors `PaperRiskManager` exactly:
  - Stateless evaluation against an explicit `LiveRiskContext` snapshot.
  - Rules return `LiveRiskCheckResult`; the first failure short-circuits.
  - Provides post-trade `should_pause_*` helpers so the service can
    auto-halt on a daily-loss or drawdown breach.

Rules:
  1. Trading is not globally halted (kill switch / `LIVE_EXEC_ENABLED`).
  2. Account is not paused (manual or auto-halt).
  3. Daily loss limit has not been breached.
  4. Max drawdown limit has not been breached.
  5. Max open positions cap not exceeded.
  6. Max trades per day cap not exceeded.
  7. No existing OPEN position for (symbol, trading_date).
  8. Per-trade exposure ≤ LIVE_EXEC_MAX_POSITION_PCT of total capital.
  9. Aggregate exposure ≤ LIVE_EXEC_MAX_CAPITAL_EXPOSURE_PCT of total capital.
  10. Broker session is healthy (caller supplies status).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Public types ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LiveRiskCheckResult:
    """Outcome of a single risk evaluation."""

    accepted: bool
    reason: Optional[str] = None
    detail: Optional[dict] = None


@dataclass(frozen=True)
class LiveRiskContext:
    """
    Snapshot the orchestrating service supplies for risk evaluation.

    Keeping the context explicit (rather than the risk manager pulling
    state) keeps the manager pure and trivially testable, and preserves
    its broker / DB independence.
    """

    symbol: str
    capital_required: float          # capital deployed by this entry (₹)
    open_position_count: int
    has_open_for_symbol: bool
    trades_taken_today: int          # filled + open + rejected? See note below
    current_exposure: float          # sum of capital across all open positions
    realized_pnl_today: float        # ₹ — net of brokerage
    unrealized_pnl: float            # ₹ — current MTM across open positions
    peak_equity: float               # highest equity seen during the session
    current_equity: float            # capital + realized + unrealized
    kill_switch_engaged: bool        # global trading halt flag
    is_account_paused: bool          # account-level pause (e.g. auto-halted)
    broker_session_healthy: bool     # caller's view of broker connectivity


# ── Risk manager ─────────────────────────────────────────────────────────────

class LiveRiskManager:
    """
    Stateless live-trading risk gatekeeper.

    Defaults pull from `settings` but every threshold is constructor-injectable
    for tests and alternate strategies.
    """

    def __init__(
        self,
        total_capital: Optional[float] = None,
        max_open_positions: Optional[int] = None,
        max_trades_per_day: Optional[int] = None,
        max_position_pct: Optional[float] = None,
        max_exposure_pct: Optional[float] = None,
        max_daily_loss_pct: Optional[float] = None,
        max_drawdown_pct: Optional[float] = None,
    ) -> None:
        self._total_capital: float = (
            total_capital if total_capital is not None else settings.LIVE_EXEC_TOTAL_CAPITAL
        )
        self._max_open: int = (
            max_open_positions
            if max_open_positions is not None
            else settings.LIVE_EXEC_MAX_OPEN_POSITIONS
        )
        self._max_trades_per_day: int = (
            max_trades_per_day
            if max_trades_per_day is not None
            else settings.LIVE_EXEC_MAX_TRADES_PER_DAY
        )
        self._max_position_pct: float = (
            max_position_pct
            if max_position_pct is not None
            else settings.LIVE_EXEC_MAX_POSITION_PCT
        )
        self._max_exposure_pct: float = (
            max_exposure_pct
            if max_exposure_pct is not None
            else settings.LIVE_EXEC_MAX_CAPITAL_EXPOSURE_PCT
        )
        self._max_daily_loss_pct: float = (
            max_daily_loss_pct
            if max_daily_loss_pct is not None
            else settings.LIVE_EXEC_MAX_DAILY_LOSS_PCT
        )
        self._max_drawdown_pct: float = (
            max_drawdown_pct
            if max_drawdown_pct is not None
            else settings.LIVE_EXEC_MAX_DRAWDOWN_PCT
        )

    # ── Public ────────────────────────────────────────────────────────────────

    def evaluate(self, context: LiveRiskContext) -> LiveRiskCheckResult:
        """Apply every rule in order and return the first failing result."""
        for rule in (
            self._check_kill_switch,
            self._check_account_paused,
            self._check_broker_session,
            self._check_duplicate_symbol,
            self._check_open_count,
            self._check_daily_trade_count,
            self._check_position_size,
            self._check_aggregate_exposure,
            self._check_daily_loss,
            self._check_drawdown,
        ):
            result = rule(context)
            if not result.accepted:
                logger.info(
                    "[live-risk] %s rejected: %s",
                    context.symbol, result.reason,
                )
                return result
        return LiveRiskCheckResult(accepted=True)

    # ── Post-trade halt helpers ───────────────────────────────────────────────

    def should_halt_for_daily_loss(self, context: LiveRiskContext) -> bool:
        threshold = -abs(self._total_capital * self._max_daily_loss_pct / 100.0)
        net = context.realized_pnl_today + context.unrealized_pnl
        return net <= threshold

    def should_halt_for_drawdown(self, context: LiveRiskContext) -> bool:
        if context.peak_equity <= 0:
            return False
        drawdown = (context.peak_equity - context.current_equity) / context.peak_equity * 100.0
        return drawdown >= self._max_drawdown_pct

    # ── Individual rules ──────────────────────────────────────────────────────

    @staticmethod
    def _check_kill_switch(context: LiveRiskContext) -> LiveRiskCheckResult:
        if context.kill_switch_engaged:
            return LiveRiskCheckResult(
                accepted=False, reason="kill_switch_engaged", detail={}
            )
        return LiveRiskCheckResult(accepted=True)

    @staticmethod
    def _check_account_paused(context: LiveRiskContext) -> LiveRiskCheckResult:
        if context.is_account_paused:
            return LiveRiskCheckResult(
                accepted=False, reason="live_trading_paused", detail={}
            )
        return LiveRiskCheckResult(accepted=True)

    @staticmethod
    def _check_broker_session(context: LiveRiskContext) -> LiveRiskCheckResult:
        if not context.broker_session_healthy:
            return LiveRiskCheckResult(
                accepted=False,
                reason="broker_session_unhealthy",
                detail={},
            )
        return LiveRiskCheckResult(accepted=True)

    @staticmethod
    def _check_duplicate_symbol(context: LiveRiskContext) -> LiveRiskCheckResult:
        if context.has_open_for_symbol:
            return LiveRiskCheckResult(
                accepted=False,
                reason="duplicate_position_for_symbol_today",
                detail={"symbol": context.symbol},
            )
        return LiveRiskCheckResult(accepted=True)

    def _check_open_count(self, context: LiveRiskContext) -> LiveRiskCheckResult:
        if context.open_position_count >= self._max_open:
            return LiveRiskCheckResult(
                accepted=False,
                reason="max_open_positions_exceeded",
                detail={
                    "open_count": context.open_position_count,
                    "max_open": self._max_open,
                },
            )
        return LiveRiskCheckResult(accepted=True)

    def _check_daily_trade_count(self, context: LiveRiskContext) -> LiveRiskCheckResult:
        if context.trades_taken_today >= self._max_trades_per_day:
            return LiveRiskCheckResult(
                accepted=False,
                reason="max_daily_trades_exceeded",
                detail={
                    "trades_today": context.trades_taken_today,
                    "max_trades": self._max_trades_per_day,
                },
            )
        return LiveRiskCheckResult(accepted=True)

    def _check_position_size(self, context: LiveRiskContext) -> LiveRiskCheckResult:
        cap_threshold = self._total_capital * self._max_position_pct / 100.0
        if context.capital_required > cap_threshold:
            return LiveRiskCheckResult(
                accepted=False,
                reason="position_size_exceeds_cap",
                detail={
                    "capital_required": context.capital_required,
                    "max_position_capital": round(cap_threshold, 4),
                },
            )
        return LiveRiskCheckResult(accepted=True)

    def _check_aggregate_exposure(self, context: LiveRiskContext) -> LiveRiskCheckResult:
        cap_threshold = self._total_capital * self._max_exposure_pct / 100.0
        projected = context.current_exposure + context.capital_required
        if projected > cap_threshold:
            return LiveRiskCheckResult(
                accepted=False,
                reason="max_capital_exposure_exceeded",
                detail={
                    "current_exposure": context.current_exposure,
                    "projected_exposure": round(projected, 4),
                    "max_exposure": round(cap_threshold, 4),
                },
            )
        return LiveRiskCheckResult(accepted=True)

    def _check_daily_loss(self, context: LiveRiskContext) -> LiveRiskCheckResult:
        if self.should_halt_for_daily_loss(context):
            return LiveRiskCheckResult(
                accepted=False,
                reason="daily_loss_limit_breached",
                detail={
                    "realized_pnl_today": context.realized_pnl_today,
                    "unrealized_pnl": context.unrealized_pnl,
                    "max_loss_pct": self._max_daily_loss_pct,
                },
            )
        return LiveRiskCheckResult(accepted=True)

    def _check_drawdown(self, context: LiveRiskContext) -> LiveRiskCheckResult:
        if self.should_halt_for_drawdown(context):
            return LiveRiskCheckResult(
                accepted=False,
                reason="max_drawdown_breached",
                detail={
                    "peak_equity": context.peak_equity,
                    "current_equity": context.current_equity,
                    "max_drawdown_pct": self._max_drawdown_pct,
                },
            )
        return LiveRiskCheckResult(accepted=True)
