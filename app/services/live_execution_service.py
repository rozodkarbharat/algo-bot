"""
Live execution service — orchestrator for the real-money execution pipeline.

This service is the ONLY layer in the live-execution stack that:
  - Touches MongoDB through the live_orders / live_positions repositories.
  - Talks to `ws_manager` (WebSocket broadcasts).
  - Bridges the live-engine callbacks (signals + candles) into the
    execution engine, position manager, risk manager, and failsafe.

Co-existence with paper trading:
  - The service registers its own callbacks on the same live signal /
    candle bus. The paper service runs in parallel and is unaffected.
  - Each `GeneratedSignal` is processed independently by both services.
  - The live service is GUARDED by `LIVE_EXEC_ENABLED`. When False, the
    execution engine short-circuits and no broker calls are made.

Broker independence:
  - The default `LiveExecutionEngine` uses the AngelOneBroker, but
    accepts any `BaseBroker` injection. Tests inject a fake broker.

WebSocket rooms broadcast by this service:
  - `live:orders`     — order lifecycle events (placed, filled, rejected, cancelled)
  - `live:positions`  — position events + per-tick MTM updates
  - `live:pnl`        — aggregated PnL snapshots
  - `live:broker`     — broker session / health changes
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.models.portfolio_allocation import PortfolioAllocation
    from app.services.portfolio_service import PortfolioService

from app.brokers.base import BaseBroker
from app.config.settings import settings
from app.core.exceptions import LivePositionNotFoundException
from app.live.candle_builder import BuiltCandle
from app.live.market_engine import LiveMarketEngine, live_market_engine
from app.live.signal_engine import GeneratedSignal
from app.live_execution.execution_engine import (
    ExecutionOutcome,
    LiveExecutionEngine,
)
from app.live_execution.failsafe import FailsafeCoordinator, failsafe
from app.live_execution.live_position_manager import (
    LiveExitDecision,
    LivePositionManager,
    LivePriceUpdate,
    ReconciliationDiff,
)
from app.live_execution.live_risk_manager import (
    LiveRiskContext,
    LiveRiskManager,
)
from app.live_execution.order_state_machine import OrderStateMachine
from app.models.live_order import (
    LiveOrder,
    LiveOrderStatus,
    LiveTradeSide,
)
from app.models.live_position import (
    LiveExitReason,
    LivePosition,
    LivePositionStatus,
)
from app.models.stock import Stock
from app.repositories.live_order_repository import LiveOrderRepository
from app.repositories.live_position_repository import LivePositionRepository
from app.repositories.stock_repository import StockRepository
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, now_utc, to_ist
from app.utils.trading_day import today_ist
from app.websocket.manager import ws_manager

logger = get_logger(__name__)


# ── WebSocket rooms ──────────────────────────────────────────────────────────

ROOM_LIVE_ORDERS = "live:orders"
ROOM_LIVE_POSITIONS = "live:positions"
ROOM_LIVE_PNL = "live:pnl"
ROOM_LIVE_BROKER = "live:broker"


# ── Result dataclasses (returned to routes) ──────────────────────────────────

@dataclass
class CloseAllResult:
    closed: int
    reason: str


@dataclass
class EngineSnapshot:
    """JSON-ready snapshot of live-execution runtime state (for the /pnl route)."""

    enabled: bool
    kill_switch: dict
    open_positions: int
    total_exposure: float
    realized_pnl_today: float
    unrealized_pnl: float
    daily_pnl: float
    total_capital: float
    peak_equity: float
    current_equity: float
    trades_today: int
    is_paused: bool
    pause_reason: Optional[str]
    broker_session_healthy: bool
    updated_at: str


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_hhmm_to_time(value: str) -> time:
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except Exception:
        return time(15, 15)


# ── Service ──────────────────────────────────────────────────────────────────

class LiveExecutionService:
    """
    Application-level coordinator for live (real-money) execution.

    Same singleton pattern as PaperTradingService — instance is shared
    across all consumers (routes, scheduler jobs, broadcasts).
    """

    def __init__(
        self,
        engine: Optional[LiveMarketEngine] = None,
        execution_engine: Optional[LiveExecutionEngine] = None,
        position_manager: Optional[LivePositionManager] = None,
        risk_manager: Optional[LiveRiskManager] = None,
        failsafe_coord: Optional[FailsafeCoordinator] = None,
        order_repo: Optional[LiveOrderRepository] = None,
        position_repo: Optional[LivePositionRepository] = None,
        stock_repo: Optional[StockRepository] = None,
        state_machine: Optional[OrderStateMachine] = None,
        broker_override: Optional[BaseBroker] = None,
        portfolio_service: Optional["PortfolioService"] = None,
    ) -> None:
        eod_time = _parse_hhmm_to_time(settings.LIVE_EXEC_EOD_EXIT_TIME_IST)

        self._engine: LiveMarketEngine = engine or live_market_engine
        self._failsafe: FailsafeCoordinator = failsafe_coord or failsafe
        self._risk: LiveRiskManager = risk_manager or LiveRiskManager()
        self._pm: LivePositionManager = position_manager or LivePositionManager(
            eod_exit_time_ist=eod_time
        )
        self._order_repo: LiveOrderRepository = order_repo or LiveOrderRepository()
        self._position_repo: LivePositionRepository = (
            position_repo or LivePositionRepository()
        )
        self._stock_repo: StockRepository = stock_repo or StockRepository()
        self._state_machine: OrderStateMachine = state_machine or OrderStateMachine(
            repo=self._order_repo
        )
        self._exec: LiveExecutionEngine = execution_engine or LiveExecutionEngine(
            broker=broker_override,
            order_repo=self._order_repo,
            stock_repo=self._stock_repo,
            state_machine=self._state_machine,
            risk_manager=self._risk,
            failsafe_coord=self._failsafe,
        )

        # Per-process runtime state (resets on each app restart — deliberately
        # so the system fails safe on a cold start).
        self._is_paused: bool = False
        self._pause_reason: Optional[str] = None
        self._realized_pnl_today: float = 0.0
        self._trades_today: int = 0
        self._peak_equity: float = settings.LIVE_EXEC_TOTAL_CAPITAL
        self._portfolio_svc: Optional["PortfolioService"] = portfolio_service
        self._wired: bool = False
        self._hydrated: bool = False
        self._mutation_lock: asyncio.Lock = asyncio.Lock()

        self._wire_pipeline_hooks()

    # ── One-time wiring ──────────────────────────────────────────────────────

    def _wire_pipeline_hooks(self) -> None:
        if self._wired:
            return
        if self._portfolio_svc is not None:
            # Route through portfolio layer — only APPROVED signals reach us.
            self._portfolio_svc.on_approved_signal(self._handle_approved_signal)
            logger.info("[live-exec] subscribed to portfolio-approved signals.")
        else:
            # Direct wiring (no portfolio layer) — kept for backward compat.
            self._engine.signal_engine.on_signal(self._handle_generated_signal)
            logger.info("[live-exec] subscribed directly to signal engine (no portfolio).")
        self._engine.candle_builder.on_candle(self._handle_built_candle)
        self._wired = True

    async def ensure_ready(self) -> None:
        """
        Idempotent boot routine.

        - Hydrates the position manager with any OPEN live positions from
          MongoDB so a deploy mid-session can recover its state.
        """
        if self._hydrated:
            return
        try:
            open_positions = await self._position_repo.get_open_positions()
            await self._pm.hydrate(open_positions)
            if open_positions:
                logger.info(
                    "[live-exec] recovered %d open live positions from MongoDB.",
                    len(open_positions),
                )
            # Refresh today's realized PnL tally from closed positions today.
            trading_dt = date_to_utc_midnight(today_ist())
            todays = await self._position_repo.get_for_date(trading_dt)
            self._realized_pnl_today = round(
                sum(
                    p.realized_pnl
                    for p in todays
                    if p.status is LivePositionStatus.CLOSED
                ),
                4,
            )
            self._trades_today = sum(
                1 for p in todays if p.status is LivePositionStatus.CLOSED
            ) + len(open_positions)
            self._hydrated = True
        except Exception as exc:
            logger.error("[live-exec] ensure_ready failed: %s", exc, exc_info=True)
            raise

    # ── Public accessors (used by routes) ────────────────────────────────────

    @property
    def execution_engine(self) -> LiveExecutionEngine:
        return self._exec

    @property
    def position_manager(self) -> LivePositionManager:
        return self._pm

    @property
    def risk_manager(self) -> LiveRiskManager:
        return self._risk

    @property
    def failsafe(self) -> FailsafeCoordinator:
        return self._failsafe

    # ── Signal handler (open position) ───────────────────────────────────────

    async def _handle_approved_signal(
        self, signal: GeneratedSignal, allocation: "PortfolioAllocation"
    ) -> None:
        """
        Portfolio-approved signal → real broker order.

        Wraps `_handle_generated_signal` and stores the portfolio-allocated
        capital so the downstream execution engine sizes correctly.
        """
        try:
            await self.ensure_ready()
            outcome = await self._open_from_signal(
                signal, portfolio_capital=allocation.allocated_capital
            )
            await self._broadcast_order_event(outcome)
            if outcome.accepted and outcome.order is not None:
                position = await self._create_position_from_filled_order(
                    signal=signal, order=outcome.order
                )
                if position is not None:
                    await self._broadcast_position_event(
                        position, event="live.position.opened"
                    )
                    await self._broadcast_pnl_snapshot()
        except Exception as exc:
            logger.error(
                "[live-exec] failed to process approved signal for %s: %s",
                signal.symbol, exc, exc_info=True,
            )

    async def _handle_generated_signal(self, signal: GeneratedSignal) -> None:
        """
        Live Signal Engine → real broker order (direct wiring, no portfolio).

        Pipeline (delegated to LiveExecutionEngine):
          1. Failsafe (kill switch, market hours, duplicate, freshness).
          2. Risk gate using a freshly-built LiveRiskContext.
          3. Persist LiveOrder (PENDING).
          4. Submit to broker.
          5. State transition to OPEN.
        Followed by:
          6. Service-side: create LivePosition, broadcast.
        """
        try:
            await self.ensure_ready()
            outcome = await self._open_from_signal(signal)
            await self._broadcast_order_event(outcome)
            if outcome.accepted and outcome.order is not None:
                position = await self._create_position_from_filled_order(
                    signal=signal, order=outcome.order
                )
                if position is not None:
                    await self._broadcast_position_event(
                        position, event="live.position.opened"
                    )
                    await self._broadcast_pnl_snapshot()
        except Exception as exc:
            logger.error(
                "[live-exec] failed to process signal for %s: %s",
                signal.symbol, exc, exc_info=True,
            )

    async def _open_from_signal(
        self,
        signal: GeneratedSignal,
        portfolio_capital: Optional[float] = None,
    ) -> ExecutionOutcome:
        async with self._mutation_lock:
            context = await self._build_risk_context(
                signal, portfolio_capital=portfolio_capital
            )
            outcome = await self._exec.execute_signal(
                signal=signal, risk_context=context
            )
            if outcome.accepted:
                self._trades_today += 1
            return outcome

    async def _create_position_from_filled_order(
        self, signal: GeneratedSignal, order: LiveOrder
    ) -> Optional[LivePosition]:
        """
        Build a LivePosition row from an order accepted by the broker.

        We optimistically create the position at OPEN-broker-acknowledgement
        time using the requested price as the entry. The polling job (see
        scheduler/jobs/live_execution_jobs.py::reconcile_order_status) will
        refine `average_price` and `quantity` once the broker reports a fill.
        """
        if order.broker_order_id is None:
            return None
        trade_side = order.trade_side
        position = LivePosition(
            broker_name=order.broker_name,
            signal_id=signal.signal_id if hasattr(signal, "signal_id") else order.signal_id,
            entry_order_id=order.order_id,
            symbol=order.symbol,
            exchange=order.exchange,
            trading_date=order.trading_date,
            trade_side=trade_side,
            quantity=order.quantity,
            average_price=order.requested_price or 0.0,
            current_price=order.requested_price or 0.0,
            stop_loss=order.stop_loss or 0.0,
            metadata={
                "broker_order_id": order.broker_order_id,
                "signal_metadata": dict(order.metadata),
            },
        )
        await self._position_repo.insert(position)
        await self._pm.add_position(position)
        logger.info(
            "[live-exec] live position opened: %s %s qty=%d entry=%.2f SL=%.2f",
            position.symbol, trade_side.value, position.quantity,
            position.average_price, position.stop_loss,
        )
        return position

    # ── Candle handler (mark-to-market + exit detection) ─────────────────────

    async def _handle_built_candle(self, candle: BuiltCandle) -> None:
        self._failsafe.feed_monitor.record_tick(symbol=candle.symbol)
        try:
            await self.ensure_ready()
        except Exception:
            return

        try:
            updates, exits = await self._pm.on_candle(candle)
        except Exception as exc:
            logger.error(
                "[live-exec] position manager error on %s candle: %s",
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
                        "[live-exec] failed to close %s: %s",
                        decision.position.symbol, exc, exc_info=True,
                    )

        if updates or exits:
            await self._broadcast_pnl_snapshot()
            await self._check_post_trade_halts()

    # ── Exit pipeline ────────────────────────────────────────────────────────

    async def _close_position(self, decision: LiveExitDecision) -> LivePosition:
        """
        Close a live position by placing an exit order at the broker.

        The actual fill price is whatever the broker reports back. The
        position is marked CLOSED immediately at the decision price; the
        polling job refines `exit_price` and `realized_pnl` on broker fill.
        """
        async with self._mutation_lock:
            position = decision.position
            stock: Optional[Stock] = await self._stock_repo.get_stock_by_symbol(
                position.symbol
            )
            if stock is None or not stock.instrument_token:
                logger.error(
                    "[live-exec] cannot exit %s: instrument_token missing",
                    position.symbol,
                )
                return position

            outcome = await self._exec.place_exit_order(
                position_symbol=position.symbol,
                instrument_token=stock.instrument_token,
                exchange=position.exchange,
                trade_side=position.trade_side,
                quantity=position.quantity,
                signal_id=position.signal_id,
                stop_loss=position.stop_loss,
                reason=decision.exit_reason.value,
                trading_date=position.trading_date,
            )

            position.status = LivePositionStatus.CLOSED
            position.exit_reason = decision.exit_reason
            position.exit_price = decision.exit_reference_price
            position.current_price = decision.exit_reference_price
            position.closed_at = decision.detected_at
            position.exit_order_id = (
                outcome.order.order_id if outcome.order is not None else None
            )
            position.realized_pnl = self._calc_pnl(
                side=position.trade_side,
                quantity=position.quantity,
                entry=position.average_price,
                exit_price=decision.exit_reference_price,
            )
            position.unrealized_pnl = 0.0
            position.metadata = {
                **position.metadata,
                "exit_reason": decision.exit_reason.value,
                "exit_outcome_accepted": outcome.accepted,
                "exit_outcome_reason": outcome.reason,
            }
            position.mark_updated()
            await self._position_repo.upsert_by_position_id(position)
            await self._pm.remove_position(position.position_id)

            self._realized_pnl_today = round(
                self._realized_pnl_today + position.realized_pnl, 4
            )
            logger.info(
                "[live-exec] closed %s %s qty=%d exit=%.2f pnl=%.2f reason=%s",
                position.symbol, position.trade_side.value, position.quantity,
                decision.exit_reference_price, position.realized_pnl,
                decision.exit_reason.value,
            )

        await self._broadcast_position_event(position, event="live.position.closed")
        return position

    async def _check_post_trade_halts(self) -> None:
        """Auto-pause when daily loss / drawdown thresholds breach."""
        ctx = await self._build_risk_context_for_halt_check()
        if self._risk.should_halt_for_daily_loss(ctx):
            await self.pause(reason="daily_loss_limit_breached")
        elif self._risk.should_halt_for_drawdown(ctx):
            await self.pause(reason="max_drawdown_breached")

    # ── Public lifecycle controls ────────────────────────────────────────────

    async def pause(self, reason: str = "manual_pause") -> dict:
        async with self._mutation_lock:
            self._is_paused = True
            self._pause_reason = reason
        await self._broadcast_engine_state(event="live.engine.paused")
        logger.warning("[live-exec] PAUSED reason=%s", reason)
        return await self._snapshot_dict()

    async def resume(self) -> dict:
        async with self._mutation_lock:
            self._is_paused = False
            self._pause_reason = None
        await self._broadcast_engine_state(event="live.engine.resumed")
        logger.info("[live-exec] resumed.")
        return await self._snapshot_dict()

    async def engage_kill_switch(self, reason: str = "manual_kill_switch") -> dict:
        """Operator emergency stop — global trading halt."""
        await self._failsafe.kill_switch.engage(reason=reason)
        await self._broadcast_engine_state(event="live.kill_switch.engaged")
        logger.warning("[live-exec] KILL SWITCH ENGAGED reason=%s", reason)
        return await self._snapshot_dict()

    async def disengage_kill_switch(self) -> dict:
        await self._failsafe.kill_switch.disengage()
        await self._broadcast_engine_state(event="live.kill_switch.disengaged")
        return await self._snapshot_dict()

    async def close_all_open(
        self, reason: LiveExitReason = LiveExitReason.MANUAL_CLOSE
    ) -> CloseAllResult:
        """Force-close every open live position via broker exit orders."""
        await self.ensure_ready()
        now = now_utc()
        if reason is LiveExitReason.EOD_EXIT:
            decisions = await self._pm.collect_eod_exits(now)
        elif reason is LiveExitReason.RISK_HALT:
            decisions = await self._pm.collect_halt_exits(now)
        else:
            # MANUAL_CLOSE / BROKER_FORCED: synthesise decisions stamped with the reason.
            decisions = await self._pm.collect_eod_exits(now)
            decisions = [
                LiveExitDecision(
                    position=d.position,
                    exit_reason=reason,
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
                    "[live-exec] close-all failed for %s: %s",
                    decision.position.symbol, exc, exc_info=True,
                )

        logger.warning(
            "[live-exec] close_all_open: %d positions closed (reason=%s).",
            len(decisions), reason.value,
        )
        await self._broadcast_pnl_snapshot()
        return CloseAllResult(closed=len(decisions), reason=reason.value)

    async def emergency_stop(self, reason: str = "operator_emergency_stop") -> dict:
        """
        Compound emergency: engage kill switch + close all open positions.

        This is the route called by `POST /api/v1/live/emergency-stop`.
        """
        await self.engage_kill_switch(reason=reason)
        result = await self.close_all_open(reason=LiveExitReason.RISK_HALT)
        snap = await self._snapshot_dict()
        snap["closed_positions"] = result.closed
        return snap

    async def reconcile_orders(self) -> dict:
        """
        Refresh the status of every non-terminal LiveOrder against the broker.

        Called periodically by the scheduler (live_execution_jobs.py). Each
        order is fetched from the broker's order book and transitioned via
        the state machine to its current status.
        """
        await self.ensure_ready()
        broker = self._exec.broker
        non_terminal = await self._order_repo.get_non_terminal(broker_name=broker.name)
        if not non_terminal:
            return {"checked": 0, "transitions": 0}

        transitions = 0
        for order in non_terminal:
            if not order.broker_order_id:
                continue
            try:
                status = await broker.get_order_status(order.broker_order_id)
                target = _broker_status_to_model(status)
                if target is None or target is order.order_status:
                    continue
                if not OrderStateMachine.is_valid_transition(order.order_status, target):
                    logger.warning(
                        "[live-exec] skipping invalid reconcile transition "
                        "%s → %s for order %s",
                        order.order_status.value, target.value, order.order_id,
                    )
                    continue
                await self._state_machine.transition(
                    order, target, reason="reconciliation",
                )
                transitions += 1
            except Exception as exc:
                logger.error(
                    "[live-exec] reconcile failed for order %s: %s",
                    order.order_id, exc,
                )
        return {"checked": len(non_terminal), "transitions": transitions}

    async def reconcile_positions(self) -> list[ReconciliationDiff]:
        """
        Cross-check the in-memory book against the broker's positions.

        The result is returned to the operator (NOT acted on automatically)
        because silent reconciliation of real-money positions is dangerous.
        """
        broker = self._exec.broker
        try:
            broker_positions = await broker.get_positions()
        except Exception as exc:
            logger.error("[live-exec] reconcile_positions: broker call failed: %s", exc)
            return []
        broker_map = {
            p.symbol.upper(): {
                "quantity": int(p.quantity),
                "average_price": float(p.average_price),
            }
            for p in broker_positions
        }
        diffs = self._pm.reconcile_with_broker(broker_map)
        if diffs:
            await ws_manager.broadcast_to_room(
                {
                    "event": "live.reconcile.diff",
                    "diffs": [d.__dict__ for d in diffs],
                    "at": now_utc().isoformat(),
                },
                ROOM_LIVE_BROKER,
            )
        return diffs

    async def refresh_broker_session(self) -> bool:
        """Force a broker re-login. Called by the session-refresh scheduler job."""
        try:
            await self._exec.broker.login()
            await ws_manager.broadcast_to_room(
                {
                    "event": "live.broker.session_refreshed",
                    "broker": self._exec.broker.name,
                    "at": now_utc().isoformat(),
                },
                ROOM_LIVE_BROKER,
            )
            return True
        except Exception as exc:
            logger.error("[live-exec] broker session refresh failed: %s", exc, exc_info=True)
            await ws_manager.broadcast_to_room(
                {
                    "event": "live.broker.session_failed",
                    "broker": self._exec.broker.name,
                    "error": str(exc),
                    "at": now_utc().isoformat(),
                },
                ROOM_LIVE_BROKER,
            )
            return False

    # ── Read API (used by routes) ────────────────────────────────────────────

    async def list_open_positions(self) -> list[LivePosition]:
        await self.ensure_ready()
        return self._pm.get_open_positions()

    async def list_positions(self, limit: int = 100, skip: int = 0) -> list[LivePosition]:
        return await self._position_repo.list_recent(limit=limit, skip=skip)

    async def list_orders(self, limit: int = 100, skip: int = 0) -> list[LiveOrder]:
        return await self._order_repo.list_recent(limit=limit, skip=skip)

    async def get_order(self, order_id: str) -> Optional[LiveOrder]:
        return await self._order_repo.get_by_order_id(order_id)

    async def get_position(self, position_id: str) -> LivePosition:
        await self.ensure_ready()
        pos = self._pm.get_position(position_id)
        if pos is not None:
            return pos
        doc = await self._position_repo.get_by_position_id(position_id)
        if doc is None:
            raise LivePositionNotFoundException(position_id)
        return doc

    async def snapshot(self) -> EngineSnapshot:
        """JSON-ready engine snapshot for `GET /api/v1/live/pnl`."""
        snap = await self._snapshot_dict()
        return EngineSnapshot(**snap)

    # ── Internal: risk context ───────────────────────────────────────────────

    async def _build_risk_context(
        self,
        signal: GeneratedSignal,
        portfolio_capital: Optional[float] = None,
    ) -> LiveRiskContext:
        symbol = signal.symbol.upper()
        broker_healthy = True
        try:
            broker_healthy = await self._exec.broker.is_connected()
        except Exception:
            broker_healthy = False

        current_exposure = self._pm.total_exposure()
        unrealized = self._pm.aggregate_unrealized_pnl()
        current_equity = (
            settings.LIVE_EXEC_TOTAL_CAPITAL
            + self._realized_pnl_today
            + unrealized
        )
        self._peak_equity = max(self._peak_equity, current_equity)

        # Honour the portfolio allocation's capital sizing if supplied.
        capital_required = (
            portfolio_capital
            if portfolio_capital is not None and portfolio_capital > 0
            else settings.LIVE_EXEC_CAPITAL_PER_TRADE
        )

        return LiveRiskContext(
            symbol=symbol,
            capital_required=capital_required,
            open_position_count=self._pm.open_count,
            has_open_for_symbol=self._pm.has_open_for_symbol(symbol),
            trades_taken_today=self._trades_today,
            current_exposure=current_exposure,
            realized_pnl_today=self._realized_pnl_today,
            unrealized_pnl=unrealized,
            peak_equity=self._peak_equity,
            current_equity=current_equity,
            kill_switch_engaged=self._failsafe.kill_switch.engaged,
            is_account_paused=self._is_paused,
            broker_session_healthy=broker_healthy,
        )

    async def _build_risk_context_for_halt_check(self) -> LiveRiskContext:
        """Lightweight context used by the post-trade halt evaluator."""
        unrealized = self._pm.aggregate_unrealized_pnl()
        current_equity = (
            settings.LIVE_EXEC_TOTAL_CAPITAL
            + self._realized_pnl_today
            + unrealized
        )
        return LiveRiskContext(
            symbol="*",
            capital_required=0.0,
            open_position_count=self._pm.open_count,
            has_open_for_symbol=False,
            trades_taken_today=self._trades_today,
            current_exposure=self._pm.total_exposure(),
            realized_pnl_today=self._realized_pnl_today,
            unrealized_pnl=unrealized,
            peak_equity=self._peak_equity,
            current_equity=current_equity,
            kill_switch_engaged=self._failsafe.kill_switch.engaged,
            is_account_paused=self._is_paused,
            broker_session_healthy=True,
        )

    # ── Internal: snapshot helper ────────────────────────────────────────────

    async def _snapshot_dict(self) -> dict:
        await self.ensure_ready()
        unrealized = self._pm.aggregate_unrealized_pnl()
        current_equity = (
            settings.LIVE_EXEC_TOTAL_CAPITAL
            + self._realized_pnl_today
            + unrealized
        )
        self._peak_equity = max(self._peak_equity, current_equity)
        try:
            broker_healthy = await self._exec.broker.is_connected()
        except Exception:
            broker_healthy = False

        return {
            "enabled": settings.LIVE_EXEC_ENABLED,
            "kill_switch": self._failsafe.kill_switch.snapshot(),
            "open_positions": self._pm.open_count,
            "total_exposure": self._pm.total_exposure(),
            "realized_pnl_today": self._realized_pnl_today,
            "unrealized_pnl": unrealized,
            "daily_pnl": round(self._realized_pnl_today + unrealized, 4),
            "total_capital": settings.LIVE_EXEC_TOTAL_CAPITAL,
            "peak_equity": self._peak_equity,
            "current_equity": current_equity,
            "trades_today": self._trades_today,
            "is_paused": self._is_paused,
            "pause_reason": self._pause_reason,
            "broker_session_healthy": broker_healthy,
            "updated_at": now_utc().isoformat(),
        }

    # ── Internal: persistence helpers ────────────────────────────────────────

    async def _persist_price_updates(self, updates: list[LivePriceUpdate]) -> None:
        if not updates:
            return
        await self._position_repo.bulk_upsert([u.position for u in updates])

    # ── Internal: broadcasts ─────────────────────────────────────────────────

    async def _broadcast_order_event(self, outcome: ExecutionOutcome) -> None:
        if outcome.order is None:
            await ws_manager.broadcast_to_room(
                {
                    "event": "live.order.suppressed",
                    "reason": outcome.reason,
                    "detail": outcome.risk_detail,
                    "at": now_utc().isoformat(),
                },
                ROOM_LIVE_ORDERS,
            )
            return
        order = outcome.order
        await ws_manager.broadcast_to_room(
            {
                "event": (
                    "live.order.placed"
                    if outcome.accepted
                    else "live.order.rejected"
                ),
                "order_id": order.order_id,
                "broker_order_id": order.broker_order_id,
                "signal_id": order.signal_id,
                "symbol": order.symbol,
                "side": order.trade_side.value,
                "quantity": order.quantity,
                "order_type": order.order_type.value,
                "status": order.order_status.value,
                "rejection_reason": order.rejection_reason,
                "at": now_utc().isoformat(),
            },
            ROOM_LIVE_ORDERS,
        )

    async def _broadcast_position_event(
        self, position: LivePosition, event: str
    ) -> None:
        await ws_manager.broadcast_to_room(
            _position_payload(position, event=event),
            ROOM_LIVE_POSITIONS,
        )

    async def _broadcast_price_updates(
        self, updates: list[LivePriceUpdate]
    ) -> None:
        await ws_manager.broadcast_to_room(
            {
                "event": "live.position.tick",
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
            ROOM_LIVE_POSITIONS,
        )

    async def _broadcast_pnl_snapshot(self) -> None:
        snap = await self._snapshot_dict()
        await ws_manager.broadcast_to_room(
            {"event": "live.pnl", **snap},
            ROOM_LIVE_PNL,
        )

    async def _broadcast_engine_state(self, event: str) -> None:
        snap = await self._snapshot_dict()
        await ws_manager.broadcast_to_room(
            {"event": event, **snap}, ROOM_LIVE_BROKER,
        )

    # ── Internal: P&L math ───────────────────────────────────────────────────

    @staticmethod
    def _calc_pnl(
        *, side: LiveTradeSide, quantity: int, entry: float, exit_price: float
    ) -> float:
        """Side-aware realised P&L (₹). Brokerage is not deducted here — the
        post-fill reconciliation step refines this once the broker's executed
        price is known."""
        delta = exit_price - entry
        if side is LiveTradeSide.SHORT:
            delta = -delta
        return round(delta * quantity, 4)


# ── Payload helpers ──────────────────────────────────────────────────────────

def _position_payload(position: LivePosition, event: str) -> dict:
    return {
        "event": event,
        "position_id": position.position_id,
        "broker_name": position.broker_name,
        "symbol": position.symbol,
        "trading_date": position.trading_date.date().isoformat(),
        "trade_side": position.trade_side.value,
        "status": position.status.value,
        "quantity": position.quantity,
        "average_price": position.average_price,
        "current_price": position.current_price,
        "stop_loss": position.stop_loss,
        "unrealized_pnl": position.unrealized_pnl,
        "realized_pnl": position.realized_pnl,
        "exit_price": position.exit_price,
        "exit_reason": position.exit_reason.value if position.exit_reason else None,
        "signal_id": position.signal_id,
        "entry_order_id": position.entry_order_id,
        "exit_order_id": position.exit_order_id,
        "opened_at": position.opened_at.isoformat(),
        "closed_at": position.closed_at.isoformat() if position.closed_at else None,
    }


def _broker_status_to_model(status) -> Optional[LiveOrderStatus]:
    """Translate the BaseBroker.OrderStatus enum into the LiveOrder model enum."""
    from app.brokers.base import OrderStatus as IfaceStatus
    mapping = {
        IfaceStatus.PENDING: LiveOrderStatus.PENDING,
        IfaceStatus.OPEN: LiveOrderStatus.OPEN,
        IfaceStatus.COMPLETE: LiveOrderStatus.FILLED,
        IfaceStatus.CANCELLED: LiveOrderStatus.CANCELLED,
        IfaceStatus.REJECTED: LiveOrderStatus.REJECTED,
    }
    return mapping.get(status)


# ── Module-level singleton ───────────────────────────────────────────────────

live_execution_service: LiveExecutionService = LiveExecutionService()
