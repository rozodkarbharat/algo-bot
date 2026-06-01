"""
Unit tests for the Broker Reconciliation Engine.

Tests cover the five core detection scenarios:
  1. Order status mismatch (STATUS_MISMATCH)
  2. Position quantity mismatch (QUANTITY_MISMATCH)
  3. Missing stop-loss (MISSING_STOP_LOSS)
  4. Rejected order (REJECTED_ORDER)
  5. Orphan broker position (ORPHAN_POSITION)

Strategy:
  - Inject mock repositories and broker so no MongoDB or broker connection is needed.
  - Each test exercises one reconciliation phase in isolation.
  - Discrepancies are captured from the service's output (the returned run + logged docs).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.brokers.base import OrderStatus, PositionInfo, ProductType
from app.models.broker_reconciliation import (
    BrokerDiscrepancy,
    BrokerReconciliationRun,
    DiscrepancyType,
    ReconciliationRunStatus,
)
from app.models.live_order import LiveOrder, LiveOrderStatus, LiveOrderType, LiveTradeSide
from app.models.live_position import LivePosition, LivePositionStatus
from app.reconciliation.broker_reconciliation_service import (
    BrokerReconciliationService,
    _STALE_PENDING_MINUTES,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_open_order(
    order_id: str = "ord001",
    broker_order_id: str = "broker001",
    symbol: str = "RELIANCE",
    status: LiveOrderStatus = LiveOrderStatus.OPEN,
    stop_loss: float = 2400.0,
    quantity: int = 10,
    filled_quantity: int = 0,
) -> LiveOrder:
    return LiveOrder.model_construct(
        order_id=order_id,
        broker_order_id=broker_order_id,
        signal_id="sig001",
        broker_name="AngelOne",
        symbol=symbol,
        exchange="NSE",
        order_type=LiveOrderType.MARKET,
        trade_side=LiveTradeSide.LONG,
        quantity=quantity,
        filled_quantity=filled_quantity,
        requested_price=None,
        executed_price=2500.0,
        stop_loss=stop_loss,
        order_status=status,
        rejection_reason=None,
        slippage=0.0,
        brokerage=20.0,
        trading_date=_utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
        created_at=_utcnow(),
        updated_at=_utcnow(),
        transitions=[],
        metadata={},
    )


def _make_open_position(
    position_id: str = "pos001",
    symbol: str = "RELIANCE",
    quantity: int = 10,
    average_price: float = 2500.0,
    current_price: float = 2520.0,
    stop_loss: float = 2400.0,
    trade_side: str = "LONG",
) -> LivePosition:
    return LivePosition.model_construct(
        position_id=position_id,
        signal_id="sig001",
        entry_order_id="ord001",
        exit_order_id=None,
        broker_name="AngelOne",
        symbol=symbol,
        exchange="NSE",
        trading_date=_utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
        trade_side=trade_side,
        quantity=quantity,
        average_price=average_price,
        current_price=current_price,
        stop_loss=stop_loss,
        unrealized_pnl=200.0,
        realized_pnl=0.0,
        status=LivePositionStatus.OPEN,
        exit_reason=None,
        exit_price=None,
        opened_at=_utcnow(),
        closed_at=None,
        updated_at=_utcnow(),
        metadata={},
    )


def _make_broker_position(
    symbol: str = "RELIANCE",
    quantity: int = 10,
    average_price: float = 2500.0,
) -> PositionInfo:
    return PositionInfo(
        symbol=symbol,
        exchange="NSE",
        product=ProductType.INTRADAY,
        quantity=quantity,
        average_price=Decimal(str(average_price)),
        last_price=Decimal("2520.00"),
        pnl=Decimal("200.00"),
    )


def _make_service_with_mocks(
    open_orders: Optional[list] = None,
    open_positions: Optional[list] = None,
    broker_order_status: OrderStatus = OrderStatus.OPEN,
    broker_positions: Optional[list] = None,
) -> tuple[BrokerReconciliationService, MagicMock, MagicMock]:
    """
    Build a BrokerReconciliationService with all repositories mocked.
    Returns (service, mock_broker, mock_run_repo).
    """
    service = BrokerReconciliationService()

    # Mock repositories
    mock_run_repo = AsyncMock()
    mock_run_repo.upsert = AsyncMock(side_effect=lambda run: run)
    mock_disc_repo = AsyncMock()
    mock_disc_repo.upsert = AsyncMock(side_effect=lambda d: d)
    mock_disc_repo.list_discrepancies = AsyncMock(return_value=[])

    mock_order_repo = AsyncMock()
    mock_order_repo.get_non_terminal = AsyncMock(return_value=open_orders or [])

    mock_pos_repo = AsyncMock()
    mock_pos_repo.get_open_positions = AsyncMock(return_value=open_positions or [])
    mock_pos_repo.get_by_position_id = AsyncMock(return_value=None)
    mock_pos_repo.upsert_by_position_id = AsyncMock()

    service._run_repo = mock_run_repo
    service._disc_repo = mock_disc_repo
    service._order_repo = mock_order_repo
    service._position_repo = mock_pos_repo

    # Mock broker
    mock_broker = AsyncMock()
    mock_broker.get_order_status = AsyncMock(return_value=broker_order_status)
    mock_broker.get_positions = AsyncMock(return_value=broker_positions or [])

    return service, mock_broker, mock_run_repo


# ── Test 1: Order Status Mismatch ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_order_status_mismatch_detected():
    """
    Internal DB shows OPEN but broker reports COMPLETE (FILLED).
    Expect a STATUS_MISMATCH discrepancy.
    """
    order = _make_open_order(status=LiveOrderStatus.OPEN)
    service, mock_broker, _ = _make_service_with_mocks(
        open_orders=[order],
        broker_order_status=OrderStatus.COMPLETE,
        broker_positions=[],
    )

    captured: list[BrokerDiscrepancy] = []

    async def capture_upsert(d):
        if isinstance(d, BrokerDiscrepancy):
            captured.append(d)
        return d

    service._disc_repo.upsert = AsyncMock(side_effect=capture_upsert)

    with (
        patch("app.reconciliation.broker_reconciliation_service.now_utc", return_value=_utcnow()),
        patch.object(service, "_generate_incidents", new=AsyncMock()),
        patch.object(service, "_send_alerts", new=AsyncMock()),
    ):
        run = await service.run_full_reconciliation(broker=mock_broker, broker_name="AngelOne")

    assert run.status == ReconciliationRunStatus.COMPLETED
    mismatch = [d for d in captured if d.discrepancy_type == DiscrepancyType.STATUS_MISMATCH]
    assert len(mismatch) == 1
    assert mismatch[0].symbol == "RELIANCE"
    assert mismatch[0].internal_value == LiveOrderStatus.OPEN
    assert mismatch[0].broker_value == OrderStatus.COMPLETE


# ── Test 2: Position Quantity Mismatch ────────────────────────────────────────

@pytest.mark.asyncio
async def test_position_quantity_mismatch_detected():
    """
    DB position holds 10 shares; broker reports 5.
    Expect a QUANTITY_MISMATCH discrepancy.
    """
    position = _make_open_position(quantity=10)
    broker_pos = _make_broker_position(symbol="RELIANCE", quantity=5)

    service, mock_broker, _ = _make_service_with_mocks(
        open_orders=[],
        open_positions=[position],
        broker_positions=[broker_pos],
    )

    captured: list[BrokerDiscrepancy] = []

    async def capture_upsert(d):
        if isinstance(d, BrokerDiscrepancy):
            captured.append(d)
        return d

    service._disc_repo.upsert = AsyncMock(side_effect=capture_upsert)

    with (
        patch("app.reconciliation.broker_reconciliation_service.now_utc", return_value=_utcnow()),
        patch.object(service, "_generate_incidents", new=AsyncMock()),
        patch.object(service, "_send_alerts", new=AsyncMock()),
    ):
        run = await service.run_full_reconciliation(broker=mock_broker, broker_name="AngelOne")

    assert run.status == ReconciliationRunStatus.COMPLETED
    qty_disc = [d for d in captured if d.discrepancy_type == DiscrepancyType.QUANTITY_MISMATCH]
    assert len(qty_disc) == 1
    assert qty_disc[0].broker_value == 5
    assert qty_disc[0].internal_value == 10
    assert qty_disc[0].symbol == "RELIANCE"


# ── Test 3: Missing Stop-Loss ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_stop_loss_detected():
    """
    Open position with stop_loss=0 (unprotected).
    Expect a CRITICAL MISSING_STOP_LOSS discrepancy.
    """
    position = _make_open_position(stop_loss=0.0)

    service, mock_broker, _ = _make_service_with_mocks(
        open_orders=[],
        open_positions=[position],
        broker_positions=[_make_broker_position()],
    )

    captured: list[BrokerDiscrepancy] = []

    async def capture_upsert(d):
        if isinstance(d, BrokerDiscrepancy):
            captured.append(d)
        return d

    service._disc_repo.upsert = AsyncMock(side_effect=capture_upsert)

    with (
        patch("app.reconciliation.broker_reconciliation_service.now_utc", return_value=_utcnow()),
        patch.object(service, "_generate_incidents", new=AsyncMock()),
        patch.object(service, "_send_alerts", new=AsyncMock()),
    ):
        run = await service.run_full_reconciliation(broker=mock_broker, broker_name="AngelOne")

    assert run.status == ReconciliationRunStatus.COMPLETED
    sl_disc = [d for d in captured if d.discrepancy_type == DiscrepancyType.MISSING_STOP_LOSS]
    assert len(sl_disc) == 1
    from app.models.alert_event import AlertSeverity
    assert sl_disc[0].severity == AlertSeverity.CRITICAL
    assert sl_disc[0].symbol == "RELIANCE"
    assert sl_disc[0].internal_value == 0.0


# ── Test 4: Rejected Order ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rejected_order_detected():
    """
    DB order is OPEN but broker reports REJECTED.
    Expect a REJECTED_ORDER discrepancy.
    """
    order = _make_open_order(status=LiveOrderStatus.OPEN)
    service, mock_broker, _ = _make_service_with_mocks(
        open_orders=[order],
        broker_order_status=OrderStatus.REJECTED,
        broker_positions=[],
    )

    captured: list[BrokerDiscrepancy] = []

    async def capture_upsert(d):
        if isinstance(d, BrokerDiscrepancy):
            captured.append(d)
        return d

    service._disc_repo.upsert = AsyncMock(side_effect=capture_upsert)

    with (
        patch("app.reconciliation.broker_reconciliation_service.now_utc", return_value=_utcnow()),
        patch.object(service, "_generate_incidents", new=AsyncMock()),
        patch.object(service, "_send_alerts", new=AsyncMock()),
    ):
        run = await service.run_full_reconciliation(broker=mock_broker, broker_name="AngelOne")

    assert run.status == ReconciliationRunStatus.COMPLETED
    rejected = [d for d in captured if d.discrepancy_type == DiscrepancyType.REJECTED_ORDER]
    assert len(rejected) == 1
    assert rejected[0].broker_value == OrderStatus.REJECTED
    assert rejected[0].internal_value == LiveOrderStatus.OPEN
    assert rejected[0].symbol == "RELIANCE"


# ── Test 5: Orphan Position at Broker ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_orphan_position_detected():
    """
    Broker holds INFOSYS shares (qty=20) but no matching LivePosition exists in DB.
    Expect a CRITICAL ORPHAN_POSITION discrepancy.
    """
    orphan_broker_pos = _make_broker_position(symbol="INFOSYS", quantity=20)

    service, mock_broker, _ = _make_service_with_mocks(
        open_orders=[],
        open_positions=[],          # DB has no INFOSYS position
        broker_positions=[orphan_broker_pos],
    )

    captured: list[BrokerDiscrepancy] = []

    async def capture_upsert(d):
        if isinstance(d, BrokerDiscrepancy):
            captured.append(d)
        return d

    service._disc_repo.upsert = AsyncMock(side_effect=capture_upsert)

    with (
        patch("app.reconciliation.broker_reconciliation_service.now_utc", return_value=_utcnow()),
        patch.object(service, "_generate_incidents", new=AsyncMock()),
        patch.object(service, "_send_alerts", new=AsyncMock()),
    ):
        run = await service.run_full_reconciliation(broker=mock_broker, broker_name="AngelOne")

    assert run.status == ReconciliationRunStatus.COMPLETED
    orphan = [d for d in captured if d.discrepancy_type == DiscrepancyType.ORPHAN_POSITION]
    assert len(orphan) == 1
    from app.models.alert_event import AlertSeverity
    assert orphan[0].severity == AlertSeverity.CRITICAL
    assert orphan[0].symbol == "INFOSYS"
    assert orphan[0].broker_value == 20
    assert orphan[0].internal_value is None


# ── Supplementary: Clean run (no discrepancies) ───────────────────────────────

@pytest.mark.asyncio
async def test_clean_run_no_discrepancies():
    """
    DB and broker are perfectly in sync.
    Expect a COMPLETED run with zero discrepancies.
    """
    order = _make_open_order(status=LiveOrderStatus.OPEN)
    position = _make_open_position()
    broker_pos = _make_broker_position()

    service, mock_broker, _ = _make_service_with_mocks(
        open_orders=[order],
        broker_order_status=OrderStatus.OPEN,
        open_positions=[position],
        broker_positions=[broker_pos],
    )

    captured: list[BrokerDiscrepancy] = []

    async def capture_upsert(d):
        if isinstance(d, BrokerDiscrepancy):
            captured.append(d)
        return d

    service._disc_repo.upsert = AsyncMock(side_effect=capture_upsert)

    with (
        patch("app.reconciliation.broker_reconciliation_service.now_utc", return_value=_utcnow()),
        patch.object(service, "_generate_incidents", new=AsyncMock()),
        patch.object(service, "_send_alerts", new=AsyncMock()),
    ):
        run = await service.run_full_reconciliation(broker=mock_broker, broker_name="AngelOne")

    assert run.status == ReconciliationRunStatus.COMPLETED
    assert len(captured) == 0
    assert run.discrepancies_found == 0


# ── Supplementary: Run continues when broker is unavailable ───────────────────

@pytest.mark.asyncio
async def test_run_without_broker_checks_sl_only():
    """
    When no broker is available (LIVE_EXEC_ENABLED=False), the service should
    still detect MISSING_STOP_LOSS via the DB-only SL phase.
    """
    position = _make_open_position(stop_loss=0.0)  # no SL

    service = BrokerReconciliationService()
    mock_run_repo = AsyncMock()
    mock_run_repo.upsert = AsyncMock(side_effect=lambda r: r)
    mock_disc_repo = AsyncMock()
    mock_disc_repo.list_discrepancies = AsyncMock(return_value=[])
    mock_order_repo = AsyncMock()
    mock_order_repo.get_non_terminal = AsyncMock(return_value=[])
    mock_pos_repo = AsyncMock()
    mock_pos_repo.get_open_positions = AsyncMock(return_value=[position])
    mock_pos_repo.get_by_position_id = AsyncMock(return_value=None)

    service._run_repo = mock_run_repo
    service._disc_repo = mock_disc_repo
    service._order_repo = mock_order_repo
    service._position_repo = mock_pos_repo

    captured: list[BrokerDiscrepancy] = []

    async def capture_upsert(d):
        if isinstance(d, BrokerDiscrepancy):
            captured.append(d)
        return d

    service._disc_repo.upsert = AsyncMock(side_effect=capture_upsert)

    with (
        patch("app.reconciliation.broker_reconciliation_service.now_utc", return_value=_utcnow()),
        patch.object(service, "_generate_incidents", new=AsyncMock()),
        patch.object(service, "_send_alerts", new=AsyncMock()),
    ):
        # Pass broker=None explicitly — simulates LIVE_EXEC_ENABLED=False
        run = await service.run_full_reconciliation(broker=None, broker_name="AngelOne")

    assert run.status == ReconciliationRunStatus.COMPLETED
    sl_disc = [d for d in captured if d.discrepancy_type == DiscrepancyType.MISSING_STOP_LOSS]
    # SL check is DB-only — should still fire even without a broker
    assert len(sl_disc) == 1
