"""
Unit tests for the live execution engine.

These tests inject fakes for the broker, repositories and state machine
so the pipeline can be exercised entirely in-process. They verify:

  - Master switch (LIVE_EXEC_ENABLED) shuts the pipeline.
  - Risk rejection short-circuits before any broker call.
  - Duplicate-signal idempotency works via the failsafe.
  - Broker rejection persists a REJECTED order through the state machine.
  - Happy path inserts a PENDING order, calls the broker, and transitions to OPEN.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

import pytest

from app.brokers.base import (
    BaseBroker,
    MarginInfo,
    OrderResponse,
    OrderSide,
    OrderStatus,
    PlaceOrderRequest,
    PositionInfo,
)
from app.config.settings import settings
from app.core.exceptions import (
    DuplicateLiveOrderException,
    OrderException,
)
from app.live.signal_engine import GeneratedSignal
from app.live_execution.execution_engine import LiveExecutionEngine
from app.live_execution.failsafe import FailsafeCoordinator, FeedMonitor, KillSwitch
from app.live_execution.live_risk_manager import LiveRiskContext, LiveRiskManager
from app.live_execution.order_state_machine import OrderStateMachine
from app.models.live_order import (
    LiveOrder,
    LiveOrderStatus,
    LiveTradeSide,
)
from app.models.live_signal import LiveBreakoutSide, LiveSignalType
from app.models.stock import Stock
from decimal import Decimal


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeBroker(BaseBroker):
    def __init__(
        self,
        broker_order_id: str = "BROKER-OK-1",
        place_raises: Optional[Exception] = None,
        connected: bool = True,
    ) -> None:
        self._broker_order_id = broker_order_id
        self._place_raises = place_raises
        self._connected = connected
        self.placed: list[PlaceOrderRequest] = []

    @property
    def name(self) -> str:
        return "FakeBroker"

    async def login(self) -> None:
        return None

    async def logout(self) -> None:
        return None

    async def is_connected(self) -> bool:
        return self._connected

    async def place_order(self, request: PlaceOrderRequest) -> OrderResponse:
        self.placed.append(request)
        if self._place_raises is not None:
            raise self._place_raises
        return OrderResponse(
            broker_order_id=self._broker_order_id,
            status=OrderStatus.OPEN,
            raw={"broker_order_id": self._broker_order_id},
        )

    async def cancel_order(self, broker_order_id: str) -> bool:
        return True

    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        return OrderStatus.OPEN

    async def get_positions(self) -> list[PositionInfo]:
        return []

    async def get_margins(self) -> MarginInfo:
        return MarginInfo(
            available_cash=Decimal("0"), used_margin=Decimal("0"), total_margin=Decimal("0"),
        )

    async def get_ltp(self, symbol: str, exchange: str) -> Decimal:
        return Decimal("2500")


class FakeOrderRepo:
    def __init__(self) -> None:
        self.inserts: list[LiveOrder] = []
        self.upserts: list[LiveOrder] = []
        self.existing_by_signal: Optional[LiveOrder] = None
        self.fail_insert: bool = False

    async def get_by_signal_and_broker(
        self, signal_id: str, broker_name: str
    ) -> Optional[LiveOrder]:
        return self.existing_by_signal

    async def insert_idempotent(self, order: LiveOrder) -> LiveOrder:
        if self.fail_insert:
            raise DuplicateLiveOrderException(identifier="signal", detail={})
        self.inserts.append(order)
        return order

    async def upsert_by_order_id(self, order: LiveOrder) -> LiveOrder:
        self.upserts.append(order)
        return order


class FakeStockRepo:
    def __init__(self, token: Optional[str] = "2885") -> None:
        self._token = token

    async def get_stock_by_symbol(self, symbol: str) -> Optional[Stock]:
        if self._token is None:
            return None
        return Stock.model_construct(
            symbol=symbol,
            exchange="NSE",
            instrument_token=self._token,
            company_name=f"{symbol} Co",
            indices=["NIFTY50"],
            sector=None,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _signal(symbol: str = "RELIANCE") -> GeneratedSignal:
    return GeneratedSignal(
        symbol=symbol,
        trading_date=date(2024, 6, 3),
        signal_type=LiveSignalType.BUY,
        breakout_side=LiveBreakoutSide.UP,
        entry_price=2500.0,
        stop_loss=2475.0,
        first_candle_high=2510.0,
        first_candle_low=2490.0,
        orb_range_percent=0.8,
        breakout_time=datetime(2024, 6, 3, 4, 30, tzinfo=timezone.utc),
        probability_score=0.7,
    )


def _risk_context(
    accepted_settings: bool = True, has_open_for_symbol: bool = False
) -> LiveRiskContext:
    return LiveRiskContext(
        symbol="RELIANCE",
        capital_required=10_000.0,
        open_position_count=0,
        has_open_for_symbol=has_open_for_symbol,
        trades_taken_today=0,
        current_exposure=0.0,
        realized_pnl_today=0.0,
        unrealized_pnl=0.0,
        peak_equity=500_000.0,
        current_equity=500_000.0,
        kill_switch_engaged=False,
        is_account_paused=False,
        broker_session_healthy=True,
    )


def _engine(
    broker: FakeBroker,
    order_repo: FakeOrderRepo,
    stock_repo: FakeStockRepo,
) -> LiveExecutionEngine:
    failsafe = FailsafeCoordinator(
        kill_switch=KillSwitch(),
        feed_monitor=FeedMonitor(staleness_threshold_seconds=300.0),
        order_repo=order_repo,  # type: ignore[arg-type]
        require_market_open=False,
    )
    state_machine = OrderStateMachine(repo=order_repo)  # type: ignore[arg-type]
    return LiveExecutionEngine(
        broker=broker,
        order_repo=order_repo,  # type: ignore[arg-type]
        stock_repo=stock_repo,  # type: ignore[arg-type]
        state_machine=state_machine,
        risk_manager=LiveRiskManager(),
        failsafe_coord=failsafe,
    )


# ── Tests ────────────────────────────────────────────────────────────────────

class TestMasterSwitch:
    @pytest.mark.asyncio
    async def test_disabled_short_circuits(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LIVE_EXEC_ENABLED", False)
        broker = FakeBroker()
        order_repo = FakeOrderRepo()
        eng = _engine(broker, order_repo, FakeStockRepo())
        outcome = await eng.execute_signal(_signal(), risk_context=_risk_context())
        assert outcome.accepted is False
        assert outcome.reason == "live_execution_disabled"
        assert broker.placed == []


class TestRiskRejection:
    @pytest.mark.asyncio
    async def test_duplicate_symbol_rejected(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LIVE_EXEC_ENABLED", True)
        broker = FakeBroker()
        order_repo = FakeOrderRepo()
        eng = _engine(broker, order_repo, FakeStockRepo())
        outcome = await eng.execute_signal(
            _signal(), risk_context=_risk_context(has_open_for_symbol=True)
        )
        assert outcome.accepted is False
        assert outcome.reason == "duplicate_position_for_symbol_today"
        assert broker.placed == []


class TestDuplicateSignal:
    @pytest.mark.asyncio
    async def test_failsafe_blocks_existing_open_order(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LIVE_EXEC_ENABLED", True)
        broker = FakeBroker()
        order_repo = FakeOrderRepo()
        # Pre-existing OPEN order for this signal must short-circuit at failsafe.
        order_repo.existing_by_signal = LiveOrder.model_construct(
            order_id="existing-1",
            broker_order_id="BR-1",
            signal_id="ignored",
            broker_name="FakeBroker",
            symbol="RELIANCE",
            exchange="NSE",
            order_type=__import__("app.models.live_order", fromlist=["LiveOrderType"]).LiveOrderType.MARKET,
            trade_side=LiveTradeSide.LONG,
            quantity=10,
            filled_quantity=0,
            requested_price=2500.0,
            executed_price=None,
            stop_loss=2475.0,
            order_status=LiveOrderStatus.OPEN,
            rejection_reason=None,
            slippage=0.0,
            brokerage=0.0,
            trading_date=datetime(2024, 6, 3, tzinfo=timezone.utc),
            transitions=[],
            metadata={},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        eng = _engine(broker, order_repo, FakeStockRepo())
        outcome = await eng.execute_signal(_signal(), risk_context=_risk_context())
        assert outcome.accepted is False
        assert outcome.reason == "duplicate_signal"
        assert broker.placed == []


class TestBrokerRejection:
    @pytest.mark.asyncio
    async def test_broker_rejection_persists_rejected_order(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LIVE_EXEC_ENABLED", True)
        broker = FakeBroker(
            place_raises=OrderException(
                broker="FakeBroker", message="insufficient_margin", detail={},
            )
        )
        order_repo = FakeOrderRepo()
        eng = _engine(broker, order_repo, FakeStockRepo())
        outcome = await eng.execute_signal(_signal(), risk_context=_risk_context())
        assert outcome.accepted is False
        assert outcome.reason == "broker_rejected"
        assert outcome.order is not None
        # An order row was inserted and then transitioned to REJECTED via state machine.
        assert len(order_repo.inserts) == 1
        assert outcome.order.order_status is LiveOrderStatus.REJECTED
        assert outcome.order.rejection_reason is not None


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_signal_placed_and_open(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LIVE_EXEC_ENABLED", True)
        broker = FakeBroker(broker_order_id="BR-OK-42")
        order_repo = FakeOrderRepo()
        eng = _engine(broker, order_repo, FakeStockRepo())
        outcome = await eng.execute_signal(_signal(), risk_context=_risk_context())
        assert outcome.accepted is True
        assert outcome.order is not None
        assert outcome.order.broker_order_id == "BR-OK-42"
        assert outcome.order.order_status is LiveOrderStatus.OPEN
        # Broker received a PlaceOrderRequest with our order_id|token tag.
        assert len(broker.placed) == 1
        assert "|" in (broker.placed[0].tag or "")

    @pytest.mark.asyncio
    async def test_missing_instrument_token_rejects(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LIVE_EXEC_ENABLED", True)
        broker = FakeBroker()
        order_repo = FakeOrderRepo()
        eng = _engine(broker, order_repo, FakeStockRepo(token=None))
        outcome = await eng.execute_signal(_signal(), risk_context=_risk_context())
        assert outcome.accepted is False
        assert outcome.reason == "instrument_token_missing"
        assert broker.placed == []
