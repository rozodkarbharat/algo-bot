"""
Unit tests for the order state machine.

Tests:
  - Valid transition graph (PENDING/OPEN/PARTIAL/FILLED/CANCELLED/REJECTED).
  - Invalid transitions raise InvalidOrderStateTransitionException.
  - Audit log is appended on every transition.
  - Side-effect fields (broker_order_id, executed_price, rejection_reason)
    are propagated to the order document.
"""

from __future__ import annotations

from typing import Optional

import pytest

from app.core.exceptions import InvalidOrderStateTransitionException
from app.live_execution.order_state_machine import OrderStateMachine
from app.models.live_order import (
    LiveOrder,
    LiveOrderStatus,
    LiveOrderType,
    LiveTradeSide,
)
from app.utils.market_time import date_to_utc_midnight, now_utc


class _FakeRepo:
    """In-memory order repo — captures upserts for assertions."""

    def __init__(self) -> None:
        self.upserts: list[LiveOrder] = []

    async def upsert_by_order_id(self, order: LiveOrder) -> LiveOrder:
        self.upserts.append(order)
        return order


def _order(status: LiveOrderStatus = LiveOrderStatus.PENDING) -> LiveOrder:
    return LiveOrder.model_construct(
        order_id="test-order-1",
        broker_order_id=None,
        signal_id="sig-1",
        broker_name="AngelOne",
        symbol="RELIANCE",
        exchange="NSE",
        order_type=LiveOrderType.MARKET,
        trade_side=LiveTradeSide.LONG,
        quantity=10,
        filled_quantity=0,
        requested_price=2500.0,
        executed_price=None,
        stop_loss=2475.0,
        order_status=status,
        rejection_reason=None,
        slippage=0.0,
        brokerage=0.0,
        trading_date=date_to_utc_midnight(now_utc().date()),
        transitions=[],
        metadata={},
        created_at=now_utc(),
        updated_at=now_utc(),
    )


def _sm() -> tuple[OrderStateMachine, _FakeRepo]:
    repo = _FakeRepo()
    return OrderStateMachine(repo=repo), repo  # type: ignore[arg-type]


class TestValidTransitions:
    @pytest.mark.asyncio
    async def test_pending_to_open(self) -> None:
        sm, repo = _sm()
        order = _order(LiveOrderStatus.PENDING)
        result = await sm.transition(
            order, LiveOrderStatus.OPEN,
            broker_order_id="BROKER-123", reason="broker_accepted",
        )
        assert result.to_state is LiveOrderStatus.OPEN
        assert order.broker_order_id == "BROKER-123"
        assert order.order_status is LiveOrderStatus.OPEN
        assert len(order.transitions) == 1
        assert order.transitions[0]["from"] == "PENDING"
        assert order.transitions[0]["to"] == "OPEN"
        assert repo.upserts == [order]

    @pytest.mark.asyncio
    async def test_open_to_partially_filled(self) -> None:
        sm, _ = _sm()
        order = _order(LiveOrderStatus.OPEN)
        await sm.transition(
            order, LiveOrderStatus.PARTIALLY_FILLED,
            filled_quantity=5, executed_price=2510.0,
        )
        assert order.order_status is LiveOrderStatus.PARTIALLY_FILLED
        assert order.filled_quantity == 5
        assert order.executed_price == 2510.0

    @pytest.mark.asyncio
    async def test_partially_filled_to_filled(self) -> None:
        sm, _ = _sm()
        order = _order(LiveOrderStatus.PARTIALLY_FILLED)
        await sm.transition(
            order, LiveOrderStatus.FILLED,
            filled_quantity=10, executed_price=2512.0,
        )
        assert order.order_status is LiveOrderStatus.FILLED
        assert order.filled_quantity == 10

    @pytest.mark.asyncio
    async def test_pending_to_rejected_records_reason(self) -> None:
        sm, _ = _sm()
        order = _order(LiveOrderStatus.PENDING)
        await sm.transition(
            order, LiveOrderStatus.REJECTED,
            rejection_reason="insufficient_margin",
        )
        assert order.order_status is LiveOrderStatus.REJECTED
        assert order.rejection_reason == "insufficient_margin"

    @pytest.mark.asyncio
    async def test_pending_to_market_filled_directly(self) -> None:
        sm, _ = _sm()
        order = _order(LiveOrderStatus.PENDING)
        await sm.transition(
            order, LiveOrderStatus.FILLED,
            broker_order_id="BROKER-X", filled_quantity=10, executed_price=2500.0,
        )
        assert order.order_status is LiveOrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_open_to_cancelled(self) -> None:
        sm, _ = _sm()
        order = _order(LiveOrderStatus.OPEN)
        await sm.transition(order, LiveOrderStatus.CANCELLED, reason="manual_cancel")
        assert order.order_status is LiveOrderStatus.CANCELLED


class TestInvalidTransitions:
    @pytest.mark.asyncio
    async def test_filled_to_open_rejected(self) -> None:
        sm, _ = _sm()
        order = _order(LiveOrderStatus.FILLED)
        with pytest.raises(InvalidOrderStateTransitionException):
            await sm.transition(order, LiveOrderStatus.OPEN)

    @pytest.mark.asyncio
    async def test_cancelled_to_filled_rejected(self) -> None:
        sm, _ = _sm()
        order = _order(LiveOrderStatus.CANCELLED)
        with pytest.raises(InvalidOrderStateTransitionException):
            await sm.transition(order, LiveOrderStatus.FILLED)

    @pytest.mark.asyncio
    async def test_rejected_to_open_rejected(self) -> None:
        sm, _ = _sm()
        order = _order(LiveOrderStatus.REJECTED)
        with pytest.raises(InvalidOrderStateTransitionException):
            await sm.transition(order, LiveOrderStatus.OPEN)


class TestAuditTrail:
    @pytest.mark.asyncio
    async def test_multiple_transitions_append_audit_rows(self) -> None:
        sm, _ = _sm()
        order = _order(LiveOrderStatus.PENDING)
        await sm.transition(order, LiveOrderStatus.OPEN, broker_order_id="B-1")
        await sm.transition(
            order, LiveOrderStatus.PARTIALLY_FILLED,
            filled_quantity=4, executed_price=2510.0,
        )
        await sm.transition(
            order, LiveOrderStatus.FILLED,
            filled_quantity=10, executed_price=2512.0,
        )
        assert len(order.transitions) == 3
        assert [t["to"] for t in order.transitions] == [
            "OPEN", "PARTIALLY_FILLED", "FILLED",
        ]


class TestValidTransitionLookup:
    def test_is_valid_transition_static(self) -> None:
        assert OrderStateMachine.is_valid_transition(
            LiveOrderStatus.PENDING, LiveOrderStatus.OPEN
        )
        assert not OrderStateMachine.is_valid_transition(
            LiveOrderStatus.FILLED, LiveOrderStatus.OPEN
        )
