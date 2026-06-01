"""
Paper trading service — orchestrator for the paper execution pipeline.

Responsibilities (all of these flow through this single class):

  1. Subscribe to the Live Signal Engine. When the live engine publishes a
     `GeneratedSignal`, the service consults the risk manager, asks the
     execution engine for a fill, persists a PaperPosition, debits the
     PaperAccount, hydrates the position manager, and broadcasts the open.
  2. Subscribe to the candle builder's `on_candle` callback. Each closed
     candle flows into the position manager which returns mark-to-market
     updates and exit decisions. The service applies exit slippage,
     calculates final PnL, writes a PaperTrade ledger row, updates the
     account, removes the position from the in-memory book and broadcasts.
  3. Provide manual control: pause/resume, force-close all, daily reset.
  4. Provide read APIs the routes call.

This class is the ONLY layer in the paper-trading stack that:
  - Touches MongoDB (via repositories).
  - Talks to `ws_manager`.
  - Bridges live-engine callbacks → paper components.

The execution engine, position manager, risk manager, pnl engine and
session manager remain pure / unit-testable.

Broker independence:
  - The service never imports `app.brokers.*`. To later swap to live
    execution, replace `_handle_generated_signal()` with a call to the
    broker adapter; everything downstream (positions, PnL, ledger) is
    already broker-agnostic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from app.config.settings import settings
from app.core.exceptions import PaperTradingException
from app.live.candle_builder import BuiltCandle
from app.live.market_engine import LiveMarketEngine, live_market_engine
from app.live.signal_engine import GeneratedSignal

if TYPE_CHECKING:
    from app.models.portfolio_allocation import PortfolioAllocation
    from app.services.portfolio_service import PortfolioService
from app.models.paper_account import DEFAULT_PAPER_ACCOUNT_ID, PaperAccount
from app.models.paper_position import (
    PaperPosition,
    PaperPositionStatus,
    PaperTradeSide,
)
from app.models.paper_trade import PaperExitReason, PaperTrade
from app.paper_trading.paper_execution_engine import PaperExecutionEngine, PaperFill
from app.paper_trading.pnl_engine import (
    apply_entry_to_account,
    apply_realized_pnl_to_account,
    calculate_pnl_percent,
    calculate_realized_pnl,
    capital_used_for_position,
    refresh_unrealized_on_account,
    roi_percent,
)
from app.paper_trading.position_manager import (
    ExitDecision,
    PaperPositionManager,
    PriceUpdate,
)
from app.paper_trading.risk_manager import (
    PaperRiskManager,
    RiskCheckResult,
    RiskContext,
)
from app.paper_trading.session_manager import PaperSessionManager
from app.repositories.paper_account_repository import PaperAccountRepository
from app.repositories.paper_position_repository import PaperPositionRepository
from app.repositories.paper_trade_repository import PaperTradeRepository
from app.utils.logger import get_logger
from app.utils.market_time import (
    date_to_utc_midnight,
    now_utc,
    to_ist,
)
from app.utils.trading_day import today_ist
from app.websocket.manager import ws_manager

logger = get_logger(__name__)


# ── WebSocket room names ─────────────────────────────────────────────────────

ROOM_PAPER_TRADES = "paper:trades"
ROOM_PAPER_POSITIONS = "paper:positions"
ROOM_PAPER_PNL = "paper:pnl"
ROOM_PAPER_ACCOUNT = "paper:account"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_hhmm_to_time(value: str):
    """Parse 'HH:MM' into a datetime.time. Falls back to 15:15 on bad input."""
    from datetime import time as _time
    try:
        hh, mm = value.split(":")
        return _time(int(hh), int(mm))
    except Exception:
        return _time(15, 15)


# ── Result dataclasses (used by routes) ──────────────────────────────────────

@dataclass
class OpenPositionResult:
    """Outcome of attempting to open a position from a signal."""

    accepted: bool
    position: Optional[PaperPosition]
    reason: Optional[str]
    risk_detail: Optional[dict] = None


@dataclass
class CloseAllResult:
    closed: int
    reason: str


# ── Service ──────────────────────────────────────────────────────────────────

class PaperTradingService:
    """
    Application-level coordinator for paper trading.

    Designed as a module-level singleton: all state lives in injected
    components (repos, engines) or in MongoDB. Multiple instances must
    not be created against the same live engine — they would each
    register signal/candle callbacks and double-process events.
    """

    def __init__(
        self,
        engine: Optional[LiveMarketEngine] = None,
        execution_engine: Optional[PaperExecutionEngine] = None,
        position_manager: Optional[PaperPositionManager] = None,
        risk_manager: Optional[PaperRiskManager] = None,
        session_manager: Optional[PaperSessionManager] = None,
        account_repo: Optional[PaperAccountRepository] = None,
        position_repo: Optional[PaperPositionRepository] = None,
        trade_repo: Optional[PaperTradeRepository] = None,
        account_id: str = DEFAULT_PAPER_ACCOUNT_ID,
        portfolio_service: Optional["PortfolioService"] = None,
    ) -> None:
        eod_time = _parse_hhmm_to_time(settings.PAPER_EOD_EXIT_TIME_IST)

        self._engine: LiveMarketEngine = engine or live_market_engine
        self._exec: PaperExecutionEngine = execution_engine or PaperExecutionEngine()
        self._pm: PaperPositionManager = position_manager or PaperPositionManager(
            eod_exit_time_ist=eod_time
        )
        self._risk: PaperRiskManager = risk_manager or PaperRiskManager()
        self._session: PaperSessionManager = (
            session_manager or PaperSessionManager(account_id=account_id)
        )

        self._account_repo: PaperAccountRepository = (
            account_repo or PaperAccountRepository()
        )
        self._position_repo: PaperPositionRepository = (
            position_repo or PaperPositionRepository()
        )
        self._trade_repo: PaperTradeRepository = trade_repo or PaperTradeRepository()

        self._account_id: str = account_id
        self._portfolio_svc: Optional["PortfolioService"] = portfolio_service
        self._wired: bool = False
        self._hydrated: bool = False
        # Mutex for service-level mutations that must not interleave
        # (open + exit on the same symbol; risk eval reads from account).
        self._mutation_lock: asyncio.Lock = asyncio.Lock()

        self._wire_pipeline_hooks()

    # ── One-time wiring ──────────────────────────────────────────────────────

    def _wire_pipeline_hooks(self) -> None:
        if self._wired:
            return
        if self._portfolio_svc is not None:
            # Route through portfolio layer — only APPROVED signals reach us.
            self._portfolio_svc.on_approved_signal(self._handle_approved_signal)
            logger.info("[paper] subscribed to portfolio-approved signals.")
        else:
            # Direct wiring (no portfolio layer) — kept for backward compat.
            self._engine.signal_engine.on_signal(self._handle_generated_signal)
            logger.info("[paper] subscribed directly to signal engine (no portfolio).")
        self._engine.candle_builder.on_candle(self._handle_built_candle)
        self._wired = True

    async def ensure_ready(self) -> PaperAccount:
        """
        Idempotent boot routine.

        - Creates the default PaperAccount if absent.
        - Hydrates the position manager with any OPEN positions from
          MongoDB (recovers state after a deploy mid-session).
        """
        account = await self._session.get_or_create_account()
        if not self._hydrated:
            open_positions = await self._position_repo.get_open_positions()
            await self._pm.hydrate(open_positions)
            self._hydrated = True
            if open_positions:
                logger.info(
                    "[paper] recovered %d open positions from MongoDB.",
                    len(open_positions),
                )
        return account

    # ── Exposed accessors (used by routes) ───────────────────────────────────

    @property
    def execution_engine(self) -> PaperExecutionEngine:
        return self._exec

    @property
    def position_manager(self) -> PaperPositionManager:
        return self._pm

    @property
    def risk_manager(self) -> PaperRiskManager:
        return self._risk

    @property
    def session_manager(self) -> PaperSessionManager:
        return self._session

    # ── Signal handler (open position) ───────────────────────────────────────

    async def _handle_approved_signal(
        self, signal: GeneratedSignal, allocation: "PortfolioAllocation"
    ) -> None:
        """
        Portfolio-approved signal → paper position.

        Wraps `_handle_generated_signal` with portfolio capital enforcement:
        the execution engine is capped to allocation.allocated_capital so the
        position size reflects the portfolio layer's decision.
        """
        try:
            await self.ensure_ready()
            await self._open_from_signal(
                signal, portfolio_capital=allocation.allocated_capital
            )
        except Exception as exc:
            logger.error(
                "[paper] failed to process approved signal for %s: %s",
                signal.symbol, exc, exc_info=True,
            )

    async def _handle_generated_signal(self, signal: GeneratedSignal) -> None:
        """
        Live Signal Engine → paper position (direct wiring, no portfolio).

        Pipeline:
          1. Ensure account / book are hydrated.
          2. Build a RiskContext snapshot.
          3. Ask the risk manager to evaluate. On reject, log + drop.
          4. Ask the execution engine to simulate a fill.
          5. Persist the new PaperPosition + debit account.
          6. Hydrate the position manager.
          7. Broadcast.
        """
        try:
            await self.ensure_ready()
            await self._open_from_signal(signal)
        except Exception as exc:
            logger.error(
                "[paper] failed to process signal for %s: %s",
                signal.symbol, exc, exc_info=True,
            )

    async def _open_from_signal(
        self,
        signal: GeneratedSignal,
        portfolio_capital: Optional[float] = None,
    ) -> OpenPositionResult:
        symbol = signal.symbol.upper()
        trading_dt = date_to_utc_midnight(signal.trading_date)

        async with self._mutation_lock:
            account = await self._session.get_or_create_account()

            context = RiskContext(
                symbol=symbol,
                capital_required=self._exec.capital_per_trade,
                open_position_count=self._pm.open_count,
                has_open_for_symbol=self._pm.has_open_for_symbol(symbol),
                trades_taken_today=await self._trade_repo.count_for_date(trading_dt)
                + self._pm.open_count,
            )

            risk: RiskCheckResult = self._risk.evaluate(account, context)
            if not risk.accepted:
                logger.info(
                    "[paper] signal rejected for %s: %s",
                    symbol, risk.reason,
                )
                return OpenPositionResult(
                    accepted=False,
                    position=None,
                    reason=risk.reason,
                    risk_detail=risk.detail,
                )

            # Cap available capital to the portfolio allocation if provided.
            effective_capital = (
                min(account.available_capital, portfolio_capital)
                if portfolio_capital is not None and portfolio_capital > 0
                else account.available_capital
            )
            fill: PaperFill = self._exec.simulate_fill(
                signal=signal,
                trading_dt_utc=trading_dt,
                available_capital=effective_capital,
            )

            if fill.filled_quantity <= 0:
                logger.warning("[paper] zero-quantity fill for %s — skipped.", symbol)
                return OpenPositionResult(
                    accepted=False, position=None, reason="zero_quantity_fill"
                )

            position = PaperPosition(
                symbol=symbol,
                trading_date=trading_dt,
                trade_side=fill.trade_side,
                quantity=fill.filled_quantity,
                entry_price=fill.entry_price,
                current_price=fill.entry_price,
                stop_loss=fill.stop_loss,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
                status=PaperPositionStatus.OPEN,
                signal_id=fill.signal_id,
                strategy_id=fill.strategy_id,
                strategy_name=fill.strategy_name,
                opened_at=fill.fill_time,
                metadata={
                    **fill.metadata,
                    "entry_brokerage": fill.entry_brokerage,
                    "slippage_per_share": fill.slippage_per_share,
                },
            )
            await self._position_repo.insert(position)
            await self._pm.add_position(position)

            apply_entry_to_account(account, fill.capital_used)
            await self._account_repo.upsert(account)

        await self._broadcast_position_opened(position)
        await self._broadcast_account_state(account)
        return OpenPositionResult(accepted=True, position=position, reason=None)

    # ── Candle handler (mark-to-market + exit detection) ─────────────────────

    async def _handle_built_candle(self, candle: BuiltCandle) -> None:
        try:
            await self.ensure_ready()
        except Exception:
            return  # DB unavailable — drop tick to avoid corrupting state

        try:
            updates, exits = await self._pm.on_candle(candle)
        except Exception as exc:
            logger.error(
                "[paper] position manager error on %s candle: %s",
                candle.symbol, exc, exc_info=True,
            )
            return

        if updates:
            await self._persist_price_updates(updates)
            await self._broadcast_price_updates(updates)

        if exits:
            for decision in exits:
                try:
                    await self._close_position(decision)
                except Exception as exc:
                    logger.error(
                        "[paper] failed to close %s: %s",
                        decision.position.symbol, exc, exc_info=True,
                    )

        if updates or exits:
            account = await self._refresh_account_unrealized()
            await self._broadcast_pnl_snapshot(account)

    # ── Exit pipeline ────────────────────────────────────────────────────────

    async def _close_position(self, decision: ExitDecision) -> PaperPosition:
        """
        Close a position based on an ExitDecision from the position manager.

        Applies exit slippage, computes net P&L, writes the PaperTrade row,
        debits/credits the account, removes the position from the book and
        broadcasts.
        """
        async with self._mutation_lock:
            position = decision.position
            side = position.trade_side

            # Apply exit slippage (always adverse) to the reference price.
            exit_price = self._exec.apply_exit_slippage(
                decision.exit_reference_price, side
            )
            brokerage_total = self._exec.brokerage_per_side * 2  # entry + exit
            pnl = calculate_realized_pnl(
                trade_side=side,
                quantity=position.quantity,
                entry_price=position.entry_price,
                exit_price=exit_price,
                brokerage_total=brokerage_total,
            )
            capital_deployed = capital_used_for_position(position)
            pnl_percent = calculate_pnl_percent(pnl, capital_deployed)

            slippage_total = round(
                (self._exec.slippage_pct / 100.0)
                * (position.entry_price + exit_price)
                * position.quantity,
                4,
            )

            # ── Update position in MongoDB (transition to CLOSED) ────────────
            position.status = PaperPositionStatus.CLOSED
            position.current_price = exit_price
            position.unrealized_pnl = 0.0
            position.realized_pnl = pnl
            position.closed_at = decision.detected_at
            position.metadata = {
                **position.metadata,
                "exit_reason": decision.exit_reason.value,
                "exit_brokerage": self._exec.brokerage_per_side,
                "exit_slippage_per_share": round(
                    decision.exit_reference_price - exit_price
                    if side is PaperTradeSide.LONG
                    else exit_price - decision.exit_reference_price,
                    4,
                ),
            }
            position.mark_updated()
            await self._position_repo.upsert_by_position_id(position)

            # ── Append immutable trade ledger row ────────────────────────────
            trade = PaperTrade(
                position_id=position.position_id,
                signal_id=position.signal_id,
                symbol=position.symbol,
                trading_date=position.trading_date,
                trade_side=side,
                quantity=position.quantity,
                entry_price=position.entry_price,
                exit_price=exit_price,
                stop_loss=position.stop_loss,
                exit_reason=decision.exit_reason,
                slippage=slippage_total,
                brokerage=brokerage_total,
                pnl=pnl,
                pnl_percent=pnl_percent,
                opened_at=position.opened_at,
                closed_at=decision.detected_at,
                strategy_id=position.strategy_id,
                strategy_name=position.strategy_name,
                metadata=position.metadata,
            )
            await self._trade_repo.insert(trade)

            # ── Update account totals + remove from book ─────────────────────
            account = await self._session.get_or_create_account()
            apply_realized_pnl_to_account(account, trade)
            await self._account_repo.upsert(account)

            await self._pm.remove_position(position.position_id)

            logger.info(
                "[paper] closed %s %s qty=%d exit=%.2f pnl=%.2f reason=%s",
                position.symbol, side.value, position.quantity,
                exit_price, pnl, decision.exit_reason.value,
            )

        await self._broadcast_position_closed(position, trade)
        await self._broadcast_account_state(account)

        # Auto-pause if risk thresholds were breached after this trade.
        if self._risk.should_pause_for_daily_loss(account):
            await self.pause(reason="daily_loss_limit_breached")
        elif self._risk.should_pause_for_consecutive_losses(account):
            await self.pause(reason="consecutive_loss_cooldown")

        return position

    # ── Public lifecycle helpers ─────────────────────────────────────────────

    async def pause(self, reason: str = "manual_pause") -> PaperAccount:
        account = await self._session.pause(reason)
        await self._broadcast_account_state(account)
        return account

    async def resume(self) -> PaperAccount:
        account = await self._session.resume()
        await self._broadcast_account_state(account)
        return account

    async def reset_daily(self, trading_date: Optional[date] = None) -> PaperAccount:
        """Reset per-day counters. Open positions are NOT auto-closed."""
        account = await self._session.reset_daily_state(trading_date)
        await self._broadcast_account_state(account)
        return account

    async def hard_reset(self) -> PaperAccount:
        """
        Wipe the account back to settings defaults.

        Forces an EOD close of every open position before resetting.
        Trade ledger rows are preserved as historical record.
        """
        await self.close_all_open(reason=PaperExitReason.MANUAL_CLOSE)
        # Also blow away today's PaperPosition rows for a fully clean slate.
        trading_dt = date_to_utc_midnight(today_ist())
        await self._position_repo.delete_for_date(trading_dt)
        await self._pm.hydrate([])
        account = await self._session.hard_reset()
        await self._broadcast_account_state(account)
        return account

    async def close_all_open(
        self, reason: PaperExitReason = PaperExitReason.EOD_EXIT
    ) -> CloseAllResult:
        """
        Force-close every open position at its current mark price.

        Used by the EOD scheduler job and the risk-halt path.
        """
        decisions = (
            await self._pm.collect_eod_exits(now_utc())
            if reason is PaperExitReason.EOD_EXIT
            else await self._pm.collect_halt_exits(now_utc())
        )
        # Override the reason on each decision (collect_eod_exits stamps EOD;
        # collect_halt_exits stamps RISK_HALT — but MANUAL_CLOSE must be
        # represented as MANUAL_CLOSE explicitly here).
        if reason is PaperExitReason.MANUAL_CLOSE:
            decisions = [
                ExitDecision(
                    position=d.position,
                    exit_reason=PaperExitReason.MANUAL_CLOSE,
                    exit_reference_price=d.exit_reference_price,
                    detected_at=d.detected_at,
                )
                for d in decisions
            ]

        for decision in decisions:
            try:
                await self._close_position(decision)
            except Exception as exc:
                logger.error(
                    "[paper] close-all failed for %s: %s",
                    decision.position.symbol, exc, exc_info=True,
                )
        logger.info("[paper] close_all_open closed %d positions (reason=%s).",
                    len(decisions), reason.value)
        return CloseAllResult(closed=len(decisions), reason=reason.value)

    # ── Read API (used by routes) ────────────────────────────────────────────

    async def get_account(self) -> PaperAccount:
        return await self.ensure_ready()

    async def list_open_positions(self) -> list[PaperPosition]:
        await self.ensure_ready()
        return self._pm.get_open_positions()

    async def list_recent_trades(self, limit: int = 100, skip: int = 0) -> list[PaperTrade]:
        return await self._trade_repo.list_recent(limit=limit, skip=skip)

    async def list_positions(self, limit: int = 100, skip: int = 0) -> list[PaperPosition]:
        return await self._position_repo.list_recent(limit=limit, skip=skip)

    async def pnl_snapshot(self) -> dict:
        """Build a JSON-ready snapshot of live PnL state."""
        await self.ensure_ready()
        account = await self._session.get_or_create_account()
        total_pnl = round(account.realized_pnl + account.unrealized_pnl, 4)
        return {
            "account_id": account.account_id,
            "starting_capital": account.starting_capital,
            "available_capital": account.available_capital,
            "used_capital": account.used_capital,
            "realized_pnl": account.realized_pnl,
            "unrealized_pnl": account.unrealized_pnl,
            "daily_pnl": account.daily_pnl,
            "total_pnl": total_pnl,
            "roi_percent": roi_percent(account.starting_capital, total_pnl),
            "open_positions": self._pm.open_count,
            "total_trades": account.total_trades,
            "winning_trades": account.winning_trades,
            "losing_trades": account.losing_trades,
            "consecutive_losses": account.consecutive_losses,
            "is_paused": account.is_paused,
            "pause_reason": account.pause_reason,
            "updated_at": account.updated_at.isoformat(),
        }

    # ── Internal: persistence + broadcast ────────────────────────────────────

    async def _persist_price_updates(self, updates: list[PriceUpdate]) -> None:
        """Bulk-write MTM-updated positions to MongoDB."""
        if not updates:
            return
        await self._position_repo.bulk_upsert([u.position for u in updates])

    async def _refresh_account_unrealized(self) -> PaperAccount:
        """Re-aggregate unrealized P&L on the account row."""
        account = await self._session.get_or_create_account()
        open_positions = self._pm.get_open_positions()
        refresh_unrealized_on_account(account, open_positions)
        await self._account_repo.upsert(account)
        return account

    async def _broadcast_position_opened(self, position: PaperPosition) -> None:
        payload = _position_payload(position, event="paper.position.opened")
        await ws_manager.broadcast_to_room(payload, ROOM_PAPER_POSITIONS)
        await ws_manager.broadcast_to_room(
            {**payload, "event": "paper.trade.opened"}, ROOM_PAPER_TRADES
        )

    async def _broadcast_position_closed(
        self, position: PaperPosition, trade: PaperTrade
    ) -> None:
        await ws_manager.broadcast_to_room(
            _position_payload(position, event="paper.position.closed"),
            ROOM_PAPER_POSITIONS,
        )
        await ws_manager.broadcast_to_room(
            _trade_payload(trade), ROOM_PAPER_TRADES
        )

    async def _broadcast_price_updates(self, updates: list[PriceUpdate]) -> None:
        await ws_manager.broadcast_to_room(
            {
                "event": "paper.position.tick",
                "updates": [
                    {
                        "position_id": u.position.position_id,
                        "symbol": u.position.symbol,
                        "previous_price": u.previous_price,
                        "current_price": u.new_price,
                        "unrealized_pnl": u.position.unrealized_pnl,
                    }
                    for u in updates
                ],
                "at": now_utc().isoformat(),
            },
            ROOM_PAPER_POSITIONS,
        )

    async def _broadcast_pnl_snapshot(self, account: PaperAccount) -> None:
        await ws_manager.broadcast_to_room(
            {
                "event": "paper.pnl",
                "realized_pnl": account.realized_pnl,
                "unrealized_pnl": account.unrealized_pnl,
                "daily_pnl": account.daily_pnl,
                "available_capital": account.available_capital,
                "open_positions": self._pm.open_count,
                "at": now_utc().isoformat(),
            },
            ROOM_PAPER_PNL,
        )

    async def _broadcast_account_state(self, account: PaperAccount) -> None:
        await ws_manager.broadcast_to_room(
            {
                "event": "paper.account",
                "account_id": account.account_id,
                "starting_capital": account.starting_capital,
                "available_capital": account.available_capital,
                "used_capital": account.used_capital,
                "realized_pnl": account.realized_pnl,
                "unrealized_pnl": account.unrealized_pnl,
                "daily_pnl": account.daily_pnl,
                "total_trades": account.total_trades,
                "winning_trades": account.winning_trades,
                "losing_trades": account.losing_trades,
                "consecutive_losses": account.consecutive_losses,
                "is_paused": account.is_paused,
                "pause_reason": account.pause_reason,
                "updated_at": account.updated_at.isoformat(),
            },
            ROOM_PAPER_ACCOUNT,
        )


# ── Payload helpers ──────────────────────────────────────────────────────────

def _position_payload(position: PaperPosition, event: str) -> dict:
    return {
        "event": event,
        "position_id": position.position_id,
        "symbol": position.symbol,
        "trading_date": position.trading_date.date().isoformat(),
        "trade_side": position.trade_side.value,
        "status": position.status.value,
        "quantity": position.quantity,
        "entry_price": position.entry_price,
        "current_price": position.current_price,
        "stop_loss": position.stop_loss,
        "unrealized_pnl": position.unrealized_pnl,
        "realized_pnl": position.realized_pnl,
        "signal_id": position.signal_id,
        "opened_at": position.opened_at.isoformat(),
        "closed_at": position.closed_at.isoformat() if position.closed_at else None,
    }


def _trade_payload(trade: PaperTrade) -> dict:
    return {
        "event": "paper.trade.closed",
        "trade_id": trade.trade_id,
        "position_id": trade.position_id,
        "signal_id": trade.signal_id,
        "symbol": trade.symbol,
        "trading_date": trade.trading_date.date().isoformat(),
        "trade_side": trade.trade_side.value,
        "quantity": trade.quantity,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "stop_loss": trade.stop_loss,
        "exit_reason": trade.exit_reason.value,
        "slippage": trade.slippage,
        "brokerage": trade.brokerage,
        "pnl": trade.pnl,
        "pnl_percent": trade.pnl_percent,
        "opened_at": trade.opened_at.isoformat(),
        "closed_at": trade.closed_at.isoformat(),
    }


# ── Module-level singleton ───────────────────────────────────────────────────

paper_trading_service: PaperTradingService = PaperTradingService()
