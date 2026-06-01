"""
Paper trading session manager — daily lifecycle and archival.

Owns the cross-session bookkeeping that does NOT belong inside the
execution / position / risk layers:

  - Initialise the default PaperAccount on first boot.
  - Reset per-day counters (daily_pnl, consecutive_losses, pause state)
    when a new trading session begins.
  - Snapshot a daily summary of the account state for reporting.

This module performs DB I/O (account upserts) and therefore depends on
the repository layer. It does not touch the broker, candles or the live
engine — its public methods are invoked by the orchestrator service or
by scheduled jobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from app.config.settings import settings
from app.models.paper_account import DEFAULT_PAPER_ACCOUNT_ID, PaperAccount
from app.repositories.paper_account_repository import PaperAccountRepository
from app.repositories.paper_position_repository import PaperPositionRepository
from app.repositories.paper_trade_repository import PaperTradeRepository
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, now_utc

logger = get_logger(__name__)


@dataclass(frozen=True)
class DailySummary:
    """Summary written at session close (used by reports + WS broadcast)."""

    trading_date: date
    starting_capital: float
    available_capital: float
    realized_pnl: float
    unrealized_pnl: float
    daily_pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int


class PaperSessionManager:
    """
    Orchestrates daily reset and EOD bookkeeping for the paper account.

    Stateless apart from constructor-injected repositories — safe to use
    as a module-level singleton.
    """

    def __init__(
        self,
        account_repo: Optional[PaperAccountRepository] = None,
        position_repo: Optional[PaperPositionRepository] = None,
        trade_repo: Optional[PaperTradeRepository] = None,
        account_id: str = DEFAULT_PAPER_ACCOUNT_ID,
    ) -> None:
        self._account_repo: PaperAccountRepository = (
            account_repo or PaperAccountRepository()
        )
        self._position_repo: PaperPositionRepository = (
            position_repo or PaperPositionRepository()
        )
        self._trade_repo: PaperTradeRepository = trade_repo or PaperTradeRepository()
        self._account_id: str = account_id

    # ── Account bootstrap ────────────────────────────────────────────────────

    async def get_or_create_account(self) -> PaperAccount:
        """
        Return the account, creating it from settings if absent.

        Used on first boot and as a safe fetch in every API route.
        """
        existing = await self._account_repo.get_by_account_id(self._account_id)
        if existing is not None:
            return existing

        starting = settings.PAPER_STARTING_CAPITAL
        account = PaperAccount(
            account_id=self._account_id,
            starting_capital=starting,
            available_capital=starting,
        )
        await self._account_repo.upsert(account)
        logger.info(
            "[paper-session] created PaperAccount account_id=%s starting_capital=%.2f",
            self._account_id, starting,
        )
        return account

    # ── Daily lifecycle ──────────────────────────────────────────────────────

    async def reset_daily_state(self, trading_date: Optional[date] = None) -> PaperAccount:
        """
        Reset per-day counters on the account and clear pause state.

        Called at the start of a new trading session (or manually via the
        /reset endpoint). Lifetime counters (realized_pnl, total_trades,
        winning_trades, losing_trades) are deliberately preserved so
        equity-curve continuity holds across sessions.
        """
        account = await self.get_or_create_account()
        account.daily_pnl = 0.0
        account.unrealized_pnl = 0.0
        account.consecutive_losses = 0
        account.is_paused = False
        account.pause_reason = None
        account.last_reset_date = date_to_utc_midnight(trading_date or now_utc().date())
        account.mark_updated()
        await self._account_repo.upsert(account)
        logger.info(
            "[paper-session] daily reset complete for account=%s on %s",
            self._account_id, account.last_reset_date.date(),
        )
        return account

    async def hard_reset(self) -> PaperAccount:
        """
        Wipe the account back to settings defaults and remove all positions.

        Trade-ledger rows are NOT deleted — they remain as historical record.
        Returns the fresh account row.
        """
        starting = settings.PAPER_STARTING_CAPITAL
        account = PaperAccount(
            account_id=self._account_id,
            starting_capital=starting,
            available_capital=starting,
        )
        await self._account_repo.upsert(account)
        logger.warning(
            "[paper-session] HARD reset: PaperAccount %s reset to starting_capital=%.2f",
            self._account_id, starting,
        )
        return account

    # ── Pause / resume ───────────────────────────────────────────────────────

    async def pause(self, reason: str) -> PaperAccount:
        account = await self.get_or_create_account()
        account.is_paused = True
        account.pause_reason = reason
        account.mark_updated()
        await self._account_repo.upsert(account)
        logger.warning("[paper-session] paused account=%s reason=%s", self._account_id, reason)
        return account

    async def resume(self) -> PaperAccount:
        account = await self.get_or_create_account()
        account.is_paused = False
        account.pause_reason = None
        account.mark_updated()
        await self._account_repo.upsert(account)
        logger.info("[paper-session] resumed account=%s", self._account_id)
        return account

    # ── EOD summary ──────────────────────────────────────────────────────────

    async def build_daily_summary(self, trading_date: date) -> DailySummary:
        """Construct (but do not persist) a summary of the day's account state."""
        account = await self.get_or_create_account()
        return DailySummary(
            trading_date=trading_date,
            starting_capital=account.starting_capital,
            available_capital=account.available_capital,
            realized_pnl=account.realized_pnl,
            unrealized_pnl=account.unrealized_pnl,
            daily_pnl=account.daily_pnl,
            total_trades=account.total_trades,
            winning_trades=account.winning_trades,
            losing_trades=account.losing_trades,
        )
