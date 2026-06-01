"""
Paper trading risk manager — pre-trade gatekeeper.

Evaluates a candidate live signal against a set of configurable rules
before the execution engine is allowed to open a position. Each rule
returns a `RiskCheckResult`; the first failure short-circuits and is the
reason returned to the service.

Rules implemented:
  1. Trading not globally paused (manual or auto-halt).
  2. Account has sufficient available capital for the deployed size.
  3. Max open positions cap not exceeded.
  4. Max trades per day cap not exceeded.
  5. No existing OPEN position for (symbol, trading_date).
  6. Per-position size <= PAPER_MAX_POSITION_PCT of starting capital.
  7. Account daily loss has not breached PAPER_MAX_DAILY_LOSS_PCT.
  8. Consecutive-loss cooldown is not active.

The risk manager itself is stateless — all inputs (account, position count,
trade count, deployed capital) come from the service so the same checks
can be applied uniformly to scheduler-driven and signal-driven entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.config.settings import settings
from app.models.paper_account import PaperAccount
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Public types ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RiskCheckResult:
    """Outcome of a single risk evaluation."""

    accepted: bool
    reason: Optional[str] = None
    detail: Optional[dict] = None


@dataclass(frozen=True)
class RiskContext:
    """
    Snapshot the service must provide for a risk evaluation.

    Keeping the context explicit (rather than the risk manager pulling
    state from repos) preserves the broker/DB-independence of this layer
    and makes tests trivial.
    """

    symbol: str
    capital_required: float       # capital_per_trade * 1 (entry-side only)
    open_position_count: int      # current number of OPEN positions
    has_open_for_symbol: bool     # already in an OPEN position today
    trades_taken_today: int       # number of CLOSED + OPEN entries today


# ── Risk manager ─────────────────────────────────────────────────────────────

class PaperRiskManager:
    """
    Stateless risk gatekeeper for paper-trading entries.

    The thresholds default to the values in `settings`, but can be overridden
    per-instance for tests or alternate accounts.
    """

    def __init__(
        self,
        max_open_positions: Optional[int] = None,
        max_trades_per_day: Optional[int] = None,
        max_daily_loss_pct: Optional[float] = None,
        consecutive_loss_cooldown: Optional[int] = None,
        max_position_pct: Optional[float] = None,
    ) -> None:
        self._max_open: int = (
            max_open_positions
            if max_open_positions is not None
            else settings.PAPER_MAX_OPEN_POSITIONS
        )
        self._max_trades_per_day: int = (
            max_trades_per_day
            if max_trades_per_day is not None
            else settings.PAPER_MAX_TRADES_PER_DAY
        )
        self._max_daily_loss_pct: float = (
            max_daily_loss_pct
            if max_daily_loss_pct is not None
            else settings.PAPER_MAX_DAILY_LOSS_PCT
        )
        self._consecutive_loss_cooldown: int = (
            consecutive_loss_cooldown
            if consecutive_loss_cooldown is not None
            else settings.PAPER_CONSECUTIVE_LOSS_COOLDOWN
        )
        self._max_position_pct: float = (
            max_position_pct
            if max_position_pct is not None
            else settings.PAPER_MAX_POSITION_PCT
        )

    # ── Public ────────────────────────────────────────────────────────────────

    def evaluate(
        self, account: PaperAccount, context: RiskContext
    ) -> RiskCheckResult:
        """
        Apply every rule in order and return the first failing result.

        Returns `RiskCheckResult(accepted=True)` only if every rule passes.
        """
        for rule in (
            self._check_paused,
            self._check_duplicate_symbol,
            self._check_open_count,
            self._check_daily_trade_count,
            self._check_position_size,
            self._check_capital,
            self._check_daily_loss,
            self._check_consecutive_losses,
        ):
            result = rule(account, context)
            if not result.accepted:
                logger.info(
                    "[risk] %s rejected: %s",
                    context.symbol, result.reason,
                )
                return result
        return RiskCheckResult(accepted=True)

    # ── Helpers exposed for the session manager ──────────────────────────────

    def should_pause_for_daily_loss(self, account: PaperAccount) -> bool:
        """Return True if the day's running P&L exceeds the daily loss cap."""
        threshold = -abs(account.starting_capital * self._max_daily_loss_pct / 100.0)
        net = account.daily_pnl + account.unrealized_pnl
        return net <= threshold

    def should_pause_for_consecutive_losses(self, account: PaperAccount) -> bool:
        """Return True if the consecutive-loss cooldown has been triggered."""
        return account.consecutive_losses >= self._consecutive_loss_cooldown

    # ── Individual rules ──────────────────────────────────────────────────────

    @staticmethod
    def _check_paused(
        account: PaperAccount, context: RiskContext
    ) -> RiskCheckResult:
        if account.is_paused:
            return RiskCheckResult(
                accepted=False,
                reason="paper_trading_paused",
                detail={"pause_reason": account.pause_reason},
            )
        return RiskCheckResult(accepted=True)

    @staticmethod
    def _check_duplicate_symbol(
        account: PaperAccount, context: RiskContext
    ) -> RiskCheckResult:
        if context.has_open_for_symbol:
            return RiskCheckResult(
                accepted=False,
                reason="duplicate_position_for_symbol_today",
                detail={"symbol": context.symbol},
            )
        return RiskCheckResult(accepted=True)

    def _check_open_count(
        self, account: PaperAccount, context: RiskContext
    ) -> RiskCheckResult:
        if context.open_position_count >= self._max_open:
            return RiskCheckResult(
                accepted=False,
                reason="max_open_positions_exceeded",
                detail={
                    "open_count": context.open_position_count,
                    "max_open": self._max_open,
                },
            )
        return RiskCheckResult(accepted=True)

    def _check_daily_trade_count(
        self, account: PaperAccount, context: RiskContext
    ) -> RiskCheckResult:
        if context.trades_taken_today >= self._max_trades_per_day:
            return RiskCheckResult(
                accepted=False,
                reason="max_daily_trades_exceeded",
                detail={
                    "trades_today": context.trades_taken_today,
                    "max_trades": self._max_trades_per_day,
                },
            )
        return RiskCheckResult(accepted=True)

    def _check_position_size(
        self, account: PaperAccount, context: RiskContext
    ) -> RiskCheckResult:
        cap_threshold = account.starting_capital * self._max_position_pct / 100.0
        if context.capital_required > cap_threshold:
            return RiskCheckResult(
                accepted=False,
                reason="position_size_exceeds_cap",
                detail={
                    "capital_required": context.capital_required,
                    "max_position_capital": round(cap_threshold, 4),
                },
            )
        return RiskCheckResult(accepted=True)

    @staticmethod
    def _check_capital(
        account: PaperAccount, context: RiskContext
    ) -> RiskCheckResult:
        if context.capital_required > account.available_capital:
            return RiskCheckResult(
                accepted=False,
                reason="insufficient_available_capital",
                detail={
                    "required": context.capital_required,
                    "available": account.available_capital,
                },
            )
        return RiskCheckResult(accepted=True)

    def _check_daily_loss(
        self, account: PaperAccount, context: RiskContext
    ) -> RiskCheckResult:
        if self.should_pause_for_daily_loss(account):
            return RiskCheckResult(
                accepted=False,
                reason="daily_loss_limit_breached",
                detail={
                    "daily_pnl": account.daily_pnl,
                    "unrealized_pnl": account.unrealized_pnl,
                    "max_loss_pct": self._max_daily_loss_pct,
                },
            )
        return RiskCheckResult(accepted=True)

    def _check_consecutive_losses(
        self, account: PaperAccount, context: RiskContext
    ) -> RiskCheckResult:
        if self.should_pause_for_consecutive_losses(account):
            return RiskCheckResult(
                accepted=False,
                reason="consecutive_loss_cooldown",
                detail={
                    "consecutive_losses": account.consecutive_losses,
                    "cooldown_threshold": self._consecutive_loss_cooldown,
                },
            )
        return RiskCheckResult(accepted=True)
