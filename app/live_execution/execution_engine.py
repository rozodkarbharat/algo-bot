"""
Live execution engine — signal → risk → broker order pipeline.

Responsibilities:
  1. Accept a `GeneratedSignal` from the live signal engine.
  2. Run failsafe (kill switch, market hours, freshness, idempotency).
  3. Run the live risk manager against a service-supplied context.
  4. Build a broker-agnostic `PlaceOrderRequest` and submit it via the
     injected `BaseBroker` (defaults to `AngelOneBroker`).
  5. Persist a `LiveOrder` row in PENDING state BEFORE calling the broker,
     transition it through the state machine on broker response.
  6. Return an `ExecutionResult` describing what happened.

The engine itself does NOT manage positions or broadcast. It returns its
outcome to the orchestrating live execution service, which then:
  - Builds the `LivePosition` row on FILLED.
  - Updates aggregate exposure / drawdown / kill switch counters.
  - Publishes WebSocket events.

Broker independence:
  - The engine depends only on `BaseBroker`, never on AngelOne specifics.
  - The Angel One adapter receives the `order_id|instrument_token` tag
    encoding that lets it construct the broker payload while preserving
    idempotency at our DB layer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from app.brokers.angelone.client import angel_one_broker
from app.brokers.base import (
    BaseBroker,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    PlaceOrderRequest,
    ProductType,
)
from app.config.settings import settings
from app.core.exceptions import (
    BrokerException,
    BrokerSessionExpiredException,
    DuplicateLiveOrderException,
    LiveRiskRejectedException,
    OrderException,
    RateLimitException,
    TradingHaltedException,
    MarketClosedException,
    StaleMarketDataException,
)
from app.live.signal_engine import GeneratedSignal
from app.live_execution.failsafe import FailsafeCoordinator, failsafe
from app.live_execution.live_risk_manager import (
    LiveRiskCheckResult,
    LiveRiskContext,
    LiveRiskManager,
)
from app.live_execution.order_state_machine import OrderStateMachine
from app.models.live_order import (
    LiveOrder,
    LiveOrderStatus,
    LiveOrderType,
    LiveTradeSide,
    _new_order_id,
)
from app.models.live_signal import LiveSignalType
from app.models.stock import Stock
from app.repositories.live_order_repository import LiveOrderRepository
from app.repositories.stock_repository import StockRepository
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, now_utc

logger = get_logger(__name__)


# ── Public dataclasses ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionOutcome:
    """Outcome of attempting to place a live order from a signal."""

    accepted: bool                            # True if a broker order was placed
    order: Optional[LiveOrder]                # the persisted LiveOrder (always set)
    reason: Optional[str] = None              # rejection reason on accepted=False
    risk_detail: Optional[dict] = None
    broker_order_id: Optional[str] = None
    raw_broker_response: dict = field(default_factory=dict)


# ── Engine ───────────────────────────────────────────────────────────────────

class LiveExecutionEngine:
    """
    Async-safe, idempotent live execution engine.

    Concurrency:
      - A per-(signal_id, broker_name) lock prevents two coroutines from
        racing to place the same order. The DB unique index is the durable
        guarantee; the in-process lock keeps the rejection path fast.
    """

    def __init__(
        self,
        broker: Optional[BaseBroker] = None,
        order_repo: Optional[LiveOrderRepository] = None,
        stock_repo: Optional[StockRepository] = None,
        state_machine: Optional[OrderStateMachine] = None,
        risk_manager: Optional[LiveRiskManager] = None,
        failsafe_coord: Optional[FailsafeCoordinator] = None,
    ) -> None:
        self._broker: BaseBroker = broker or angel_one_broker
        self._order_repo: LiveOrderRepository = order_repo or LiveOrderRepository()
        self._stock_repo: StockRepository = stock_repo or StockRepository()
        self._state_machine: OrderStateMachine = state_machine or OrderStateMachine(
            repo=self._order_repo
        )
        self._risk: LiveRiskManager = risk_manager or LiveRiskManager()
        self._failsafe: FailsafeCoordinator = failsafe_coord or failsafe
        # Per-signal lock to keep the idempotency window O(1).
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard: asyncio.Lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    async def execute_signal(
        self,
        signal: GeneratedSignal,
        *,
        risk_context: LiveRiskContext,
    ) -> ExecutionOutcome:
        """
        Run a fresh `GeneratedSignal` through the full pipeline.

        Returns an `ExecutionOutcome` describing the result. Never raises
        on rejection — callers can branch on `outcome.accepted`. Hard
        failures (broker outage, DB error) DO propagate so the service
        layer can surface them.
        """
        symbol = signal.symbol.upper()
        broker_name = self._broker.name
        signal_id = self._signal_key(signal)

        if not settings.LIVE_EXEC_ENABLED:
            logger.warning(
                "[live-exec] signal for %s dropped — LIVE_EXEC_ENABLED=False",
                symbol,
            )
            return ExecutionOutcome(
                accepted=False,
                order=None,
                reason="live_execution_disabled",
            )

        lock = await self._lock_for(signal_id, broker_name)
        async with lock:
            return await self._execute_signal_locked(
                signal=signal,
                risk_context=risk_context,
                signal_id=signal_id,
                broker_name=broker_name,
            )

    async def place_exit_order(
        self,
        *,
        position_symbol: str,
        instrument_token: str,
        exchange: str,
        trade_side: LiveTradeSide,
        quantity: int,
        signal_id: Optional[str] = None,
        stop_loss: Optional[float] = None,
        reason: str = "manual_exit",
        trading_date: Optional[datetime] = None,
    ) -> ExecutionOutcome:
        """
        Place an exit order to flatten an existing live position.

        The side flips relative to the position's trade_side: closing a
        LONG = SELL, closing a SHORT = BUY. Exits intentionally bypass the
        live risk manager (the engine MUST be able to flatten positions
        even when the account is paused / kill switch engaged) but still
        respect the order state machine and DB persistence.
        """
        order_side = (
            OrderSide.SELL if trade_side is LiveTradeSide.LONG else OrderSide.BUY
        )
        _now = now_utc()
        order = LiveOrder.model_construct(
            order_id=_new_order_id(),
            broker_name=self._broker.name,
            signal_id=signal_id,
            broker_order_id=None,
            symbol=position_symbol.upper(),
            exchange=exchange,
            order_type=_to_model_order_type(_default_order_type()),
            trade_side=trade_side,
            quantity=quantity,
            filled_quantity=0,
            requested_price=None,
            executed_price=None,
            stop_loss=stop_loss,
            order_status=LiveOrderStatus.PENDING,
            rejection_reason=None,
            slippage=0.0,
            brokerage=0.0,
            trading_date=trading_date or date_to_utc_midnight(now_utc().date()),
            transitions=[],
            metadata={"exit_reason": reason, "is_exit_order": True},
            created_at=_now,
            updated_at=_now,
        )
        try:
            await self._order_repo.upsert_by_order_id(order)
            broker_resp = await self._submit_to_broker(
                order=order,
                instrument_token=instrument_token,
                order_side=order_side,
            )
            await self._state_machine.transition(
                order,
                LiveOrderStatus.OPEN,
                broker_order_id=broker_resp.broker_order_id,
                reason=f"exit_{reason}",
                metadata={"raw": broker_resp.raw},
            )
        except OrderException as exc:
            await self._state_machine.transition(
                order,
                LiveOrderStatus.REJECTED,
                rejection_reason=str(exc.message),
                reason=f"broker_rejected_exit_{reason}",
            )
            return ExecutionOutcome(
                accepted=False, order=order, reason="broker_rejected_exit",
                raw_broker_response=exc.detail or {},
            )
        return ExecutionOutcome(
            accepted=True, order=order, broker_order_id=order.broker_order_id,
            raw_broker_response=broker_resp.raw or {},
        )

    # ── Internal: signal pipeline ─────────────────────────────────────────────

    async def _execute_signal_locked(
        self,
        signal: GeneratedSignal,
        risk_context: LiveRiskContext,
        signal_id: str,
        broker_name: str,
    ) -> ExecutionOutcome:
        symbol = signal.symbol.upper()

        # ── (1) Failsafe guards ──────────────────────────────────────────────
        try:
            await self._failsafe.ensure_safe_to_trade(
                symbol=symbol,
                signal_id=signal_id,
                broker_name=broker_name,
            )
        except DuplicateLiveOrderException as exc:
            logger.info("[live-exec] duplicate suppressed: %s", exc.message)
            return ExecutionOutcome(
                accepted=False, order=None,
                reason="duplicate_signal", risk_detail=exc.detail,
            )
        except (TradingHaltedException, MarketClosedException, StaleMarketDataException) as exc:
            logger.warning("[live-exec] failsafe blocked %s: %s", symbol, exc.message)
            return ExecutionOutcome(
                accepted=False, order=None, reason=str(exc.message),
                risk_detail=exc.detail,
            )

        # ── (2) Risk gate ─────────────────────────────────────────────────────
        risk_result: LiveRiskCheckResult = self._risk.evaluate(risk_context)
        if not risk_result.accepted:
            return ExecutionOutcome(
                accepted=False, order=None,
                reason=risk_result.reason, risk_detail=risk_result.detail,
            )

        # ── (3) Resolve instrument token + size quantity ─────────────────────
        stock: Optional[Stock] = await self._stock_repo.get_stock_by_symbol(symbol)
        if stock is None or not stock.instrument_token:
            logger.error("[live-exec] cannot resolve instrument_token for %s", symbol)
            return ExecutionOutcome(
                accepted=False, order=None, reason="instrument_token_missing",
            )

        trade_side = (
            LiveTradeSide.LONG
            if signal.signal_type is LiveSignalType.BUY
            else LiveTradeSide.SHORT
        )
        order_side = (
            OrderSide.BUY if trade_side is LiveTradeSide.LONG else OrderSide.SELL
        )
        quantity = self._size_quantity(signal.entry_price)
        if quantity <= 0:
            return ExecutionOutcome(
                accepted=False, order=None, reason="zero_quantity_sized",
            )

        # ── (4) Persist PENDING LiveOrder (idempotency boundary) ─────────────
        _now = now_utc()
        order = LiveOrder.model_construct(
            order_id=_new_order_id(),
            broker_order_id=None,
            broker_name=broker_name,
            signal_id=signal_id,
            symbol=symbol,
            exchange=stock.exchange or settings.LIVE_EXEC_DEFAULT_EXCHANGE,
            order_type=_to_model_order_type(_default_order_type()),
            trade_side=trade_side,
            quantity=quantity,
            filled_quantity=0,
            requested_price=signal.entry_price,
            executed_price=None,
            stop_loss=signal.stop_loss,
            order_status=LiveOrderStatus.PENDING,
            rejection_reason=None,
            slippage=0.0,
            brokerage=0.0,
            trading_date=date_to_utc_midnight(signal.trading_date),
            transitions=[],
            metadata={
                "orb_high": signal.first_candle_high,
                "orb_low": signal.first_candle_low,
                "orb_range_percent": signal.orb_range_percent,
                "breakout_time": signal.breakout_time.isoformat(),
                "probability_score": signal.probability_score,
                "capital_per_trade": settings.LIVE_EXEC_CAPITAL_PER_TRADE,
                "is_entry_order": True,
            },
            created_at=_now,
            updated_at=_now,
        )
        try:
            await self._order_repo.insert_idempotent(order)
        except DuplicateLiveOrderException as exc:
            # Race-lost: another coroutine inserted concurrently. Idempotency
            # holds — surface as a duplicate-suppressed outcome.
            logger.info("[live-exec] insert race lost for signal=%s", signal_id)
            return ExecutionOutcome(
                accepted=False, order=None,
                reason="duplicate_signal", risk_detail=exc.detail,
            )

        # ── (5) Submit to broker (with retry on transient failures) ──────────
        try:
            broker_resp = await self._submit_to_broker(
                order=order,
                instrument_token=stock.instrument_token,
                order_side=order_side,
            )
        except OrderException as exc:
            await self._state_machine.transition(
                order, LiveOrderStatus.REJECTED,
                rejection_reason=str(exc.message),
                reason="broker_rejected",
            )
            return ExecutionOutcome(
                accepted=False, order=order, reason="broker_rejected",
                raw_broker_response=exc.detail or {},
            )
        except (BrokerSessionExpiredException, RateLimitException, BrokerException) as exc:
            await self._state_machine.transition(
                order, LiveOrderStatus.REJECTED,
                rejection_reason=str(exc.message),
                reason="broker_unavailable",
            )
            return ExecutionOutcome(
                accepted=False, order=order, reason="broker_unavailable",
                raw_broker_response=getattr(exc, "detail", None) or {},
            )

        # ── (6) Move to OPEN and return ──────────────────────────────────────
        await self._state_machine.transition(
            order,
            LiveOrderStatus.OPEN,
            broker_order_id=broker_resp.broker_order_id,
            reason="broker_accepted",
            metadata={"raw": broker_resp.raw},
        )
        return ExecutionOutcome(
            accepted=True,
            order=order,
            broker_order_id=order.broker_order_id,
            raw_broker_response=broker_resp.raw or {},
        )

    # ── Internal: broker call ─────────────────────────────────────────────────

    async def _submit_to_broker(
        self,
        order: LiveOrder,
        instrument_token: str,
        order_side: OrderSide,
    ) -> OrderResponse:
        """Build the broker-agnostic request and submit it via the adapter."""
        tag = f"{order.order_id}|{instrument_token}"
        request = PlaceOrderRequest(
            symbol=order.symbol,
            exchange=order.exchange,
            side=order_side,
            order_type=_to_iface_order_type(order.order_type),
            product=_default_product(),
            quantity=order.quantity,
            price=(
                Decimal(str(order.requested_price))
                if order.order_type is LiveOrderType.LIMIT and order.requested_price
                else None
            ),
            trigger_price=None,
            tag=tag,
        )
        # Ensure session is healthy. Login() is a no-op when fresh.
        try:
            await self._broker.login()
        except Exception as exc:
            raise BrokerSessionExpiredException(self._broker.name) from exc

        return await self._broker.place_order(request)

    # ── Internal: helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _signal_key(signal: GeneratedSignal) -> str:
        """Stable id used for the idempotency lock + (signal_id, broker) unique index."""
        return f"{signal.symbol.upper()}|{signal.trading_date.isoformat()}|{signal.signal_type.value}"

    async def _lock_for(self, signal_id: str, broker_name: str) -> asyncio.Lock:
        key = f"{signal_id}::{broker_name}"
        async with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    @staticmethod
    def _size_quantity(entry_price: float) -> int:
        if entry_price <= 0:
            return 0
        budget = settings.LIVE_EXEC_CAPITAL_PER_TRADE
        if budget <= 0:
            return 0
        qty = int(budget // entry_price)
        return qty if qty >= 1 else 0

    @property
    def broker(self) -> BaseBroker:
        return self._broker


# ── Helpers ──────────────────────────────────────────────────────────────────

def _default_order_type() -> OrderType:
    return (
        OrderType.MARKET
        if settings.LIVE_EXEC_DEFAULT_ORDER_TYPE.upper() == "MARKET"
        else OrderType.LIMIT
    )


def _default_product() -> ProductType:
    return (
        ProductType.INTRADAY
        if settings.LIVE_EXEC_DEFAULT_PRODUCT.upper() == "INTRADAY"
        else ProductType.DELIVERY
    )


def _to_model_order_type(t: OrderType) -> LiveOrderType:
    mapping = {
        OrderType.MARKET: LiveOrderType.MARKET,
        OrderType.LIMIT: LiveOrderType.LIMIT,
        OrderType.SL: LiveOrderType.SL,
        OrderType.SL_M: LiveOrderType.SL_M,
    }
    return mapping[t]


def _to_iface_order_type(t: LiveOrderType) -> OrderType:
    mapping = {
        LiveOrderType.MARKET: OrderType.MARKET,
        LiveOrderType.LIMIT: OrderType.LIMIT,
        LiveOrderType.SL: OrderType.SL,
        LiveOrderType.SL_M: OrderType.SL_M,
    }
    return mapping[t]
