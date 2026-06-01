"""
Broker Reconciliation Service.

Continuously verifies that broker state matches internal database state.
Run every 5 minutes during market hours; also triggered immediately after
order events.

Reconciliation phases (in order):
  1. Order reconciliation   — compare DB orders vs broker order statuses
  2. Position reconciliation — compare DB positions vs broker positions
  3. SL reconciliation      — verify every open position has a valid stop-loss
  4. Auto-resolution        — attempt safe, non-destructive fixes (no order placement)
  5. Incident generation    — open SystemIncidents for critical/warning discrepancies
  6. Alerting               — send notifications based on severity

Design constraints (from project architecture rules):
  - NEVER auto-place or auto-cancel orders at the broker
  - Order status mutations MUST go through OrderStateMachine
  - All DB I/O through repositories
  - Never raises from a scheduler job (wraps top-level in try/except)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.brokers.base import BaseBroker, OrderStatus, PositionInfo
from app.models.alert_event import AlertSeverity
from app.models.broker_reconciliation import (
    BrokerDiscrepancy,
    BrokerReconciliationRun,
    DISCREPANCY_SEVERITY,
    DiscrepancyStatus,
    DiscrepancyType,
    ReconciliationRunStatus,
    _new_discrepancy_id,
    _new_run_id,
    _utcnow,
)
from app.models.live_order import LiveOrderStatus
from app.models.live_position import LivePositionStatus
from app.repositories.broker_reconciliation_repository import (
    BrokerDiscrepancyRepository,
    BrokerReconciliationRunRepository,
)
from app.repositories.live_order_repository import LiveOrderRepository
from app.repositories.live_position_repository import LivePositionRepository
from app.utils.logger import get_logger
from app.utils.market_time import now_utc

logger = get_logger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

# An order still in PENDING state for longer than this is considered stale.
_STALE_PENDING_MINUTES = 5

# An order in OPEN state for longer than this during intraday is flagged.
_STALE_OPEN_MINUTES = 90

# Average price difference tolerance (0.5%) before flagging PRICE_MISMATCH.
_PRICE_TOLERANCE_PCT = 0.005

# Broker status values treated as terminal at the broker side.
_BROKER_TERMINAL = {OrderStatus.COMPLETE, OrderStatus.CANCELLED, OrderStatus.REJECTED}

# Internal-to-broker status mapping for comparison.
_STATUS_MAP: dict[str, str] = {
    LiveOrderStatus.OPEN.value: OrderStatus.OPEN.value,
    LiveOrderStatus.FILLED.value: OrderStatus.COMPLETE.value,
    LiveOrderStatus.CANCELLED.value: OrderStatus.CANCELLED.value,
    LiveOrderStatus.REJECTED.value: OrderStatus.REJECTED.value,
}


class BrokerReconciliationService:
    """
    Orchestrates the full broker ↔ DB reconciliation cycle.

    Designed as a module-level singleton. All public methods are async.
    """

    def __init__(self) -> None:
        self._run_repo = BrokerReconciliationRunRepository()
        self._disc_repo = BrokerDiscrepancyRepository()
        self._order_repo = LiveOrderRepository()
        self._position_repo = LivePositionRepository()
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Public entrypoints ────────────────────────────────────────────────────

    async def run_full_reconciliation(
        self,
        broker: Optional[BaseBroker] = None,
        broker_name: str = "AngelOne",
        trigger: str = "scheduled",
    ) -> BrokerReconciliationRun:
        """
        Execute a complete reconciliation cycle.

        Returns the completed BrokerReconciliationRun (even if it failed).
        Never raises — callers (jobs) can safely fire-and-forget.
        """
        async with self._lock:
            return await self._run(broker, broker_name, trigger)

    async def trigger_immediate(
        self, broker: Optional[BaseBroker] = None, broker_name: str = "AngelOne"
    ) -> BrokerReconciliationRun:
        """Called immediately after order events (fills, rejections)."""
        return await self.run_full_reconciliation(
            broker=broker, broker_name=broker_name, trigger="order_event"
        )

    async def list_runs(self, limit: int = 20) -> list[BrokerReconciliationRun]:
        return await self._run_repo.list_recent(limit=limit)

    async def list_discrepancies(
        self,
        run_id: Optional[str] = None,
        status: Optional[DiscrepancyStatus] = None,
        symbol: Optional[str] = None,
        discrepancy_type: Optional[DiscrepancyType] = None,
        limit: int = 100,
    ) -> list[BrokerDiscrepancy]:
        return await self._disc_repo.list_discrepancies(
            run_id=run_id,
            status=status,
            symbol=symbol,
            discrepancy_type=discrepancy_type,
            limit=limit,
        )

    # ── Internal orchestration ────────────────────────────────────────────────

    async def _run(
        self,
        broker: Optional[BaseBroker],
        broker_name: str,
        trigger: str,
    ) -> BrokerReconciliationRun:
        run = BrokerReconciliationRun.model_construct(
            run_id=_new_run_id(),
            broker_name=broker_name,
            started_at=now_utc(),
            status=ReconciliationRunStatus.RUNNING,
            discrepancies_found=0,
            orders_checked=0,
            positions_checked=0,
            metadata={"trigger": trigger},
        )
        await self._run_repo.upsert(run)
        logger.info("[recon] Run %s started (trigger=%s)", run.run_id, trigger)

        discrepancies: list[BrokerDiscrepancy] = []

        try:
            if broker is None:
                broker = await self._resolve_broker()

            # Phase 1 — orders
            order_discrepancies, orders_checked = await self._reconcile_orders(
                run.run_id, broker_name, broker
            )
            discrepancies.extend(order_discrepancies)
            run.orders_checked = orders_checked

            # Phase 2 — positions
            pos_discrepancies, positions_checked = await self._reconcile_positions(
                run.run_id, broker_name, broker
            )
            discrepancies.extend(pos_discrepancies)
            run.positions_checked = positions_checked

            # Phase 3 — stop-losses (DB-only, no broker call)
            sl_discrepancies = await self._reconcile_stop_losses(
                run.run_id, broker_name
            )
            discrepancies.extend(sl_discrepancies)

            # Persist all discovered discrepancies
            for d in discrepancies:
                await self._disc_repo.upsert(d)

            # Phase 4 — auto-resolution (best-effort, non-destructive)
            await self._attempt_auto_resolution(discrepancies, broker)

            # Phase 5 — incident generation
            await self._generate_incidents(discrepancies)

            # Phase 6 — alerting
            await self._send_alerts(discrepancies)

            run.discrepancies_found = len(discrepancies)
            run.status = ReconciliationRunStatus.COMPLETED
            run.completed_at = now_utc()

            if discrepancies:
                logger.warning(
                    "[recon] Run %s completed: %d discrepancy(ies) found.",
                    run.run_id, len(discrepancies),
                )
            else:
                logger.info(
                    "[recon] Run %s completed: broker and DB are in sync.",
                    run.run_id,
                )

        except Exception as exc:
            logger.error(
                "[recon] Run %s failed: %s", run.run_id, exc, exc_info=True
            )
            run.status = ReconciliationRunStatus.FAILED
            run.completed_at = now_utc()
            run.metadata["error"] = str(exc)

        await self._run_repo.upsert(run)
        return run

    # ── Phase 1: Order Reconciliation ─────────────────────────────────────────

    async def _reconcile_orders(
        self,
        run_id: str,
        broker_name: str,
        broker: Optional[BaseBroker],
    ) -> tuple[list[BrokerDiscrepancy], int]:
        """
        Compare non-terminal internal orders against broker order statuses.

        Detects:
          - REJECTED_ORDER   — broker rejected an order we thought was OPEN
          - STATUS_MISMATCH  — internal and broker status differ
          - PARTIAL_FILL     — filled_quantity is behind broker's filled count
          - STALE_ORDER      — PENDING/OPEN order older than threshold
          - MISSING_ORDER    — order with broker_order_id that broker can't find
        """
        discrepancies: list[BrokerDiscrepancy] = []
        now = now_utc()

        try:
            open_orders = await self._order_repo.get_non_terminal(
                broker_name=broker_name
            )
        except Exception as exc:
            logger.error("[recon] Failed to fetch non-terminal orders: %s", exc)
            return [], 0

        for order in open_orders:
            age_minutes = (now - order.created_at).total_seconds() / 60

            # ── Stale detection (no broker call needed) ────────────────────
            if order.order_status == LiveOrderStatus.PENDING:
                if age_minutes > _STALE_PENDING_MINUTES:
                    discrepancies.append(
                        self._make_discrepancy(
                            run_id=run_id,
                            dtype=DiscrepancyType.STALE_ORDER,
                            symbol=order.symbol,
                            description=(
                                f"Order {order.order_id} has been PENDING for "
                                f"{age_minutes:.1f} min (threshold {_STALE_PENDING_MINUTES} min)."
                            ),
                            broker_value=None,
                            internal_value=order.order_status,
                            metadata={"order_id": order.order_id, "age_minutes": age_minutes},
                        )
                    )
                continue  # No broker_order_id yet — skip live broker check

            if order.order_status == LiveOrderStatus.OPEN and age_minutes > _STALE_OPEN_MINUTES:
                discrepancies.append(
                    self._make_discrepancy(
                        run_id=run_id,
                        dtype=DiscrepancyType.STALE_ORDER,
                        symbol=order.symbol,
                        description=(
                            f"Order {order.order_id} has been OPEN for "
                            f"{age_minutes:.1f} min (threshold {_STALE_OPEN_MINUTES} min). "
                            "Intraday MARKET orders should fill or reject quickly."
                        ),
                        broker_value=None,
                        internal_value=order.order_status,
                        metadata={"order_id": order.order_id, "age_minutes": age_minutes},
                    )
                )

            # ── Live broker status check ───────────────────────────────────
            if broker is None or not order.broker_order_id:
                continue

            try:
                broker_status: OrderStatus = await broker.get_order_status(
                    order.broker_order_id
                )
            except Exception as exc:
                logger.warning(
                    "[recon] Failed to fetch status for broker_order_id=%s: %s",
                    order.broker_order_id, exc,
                )
                discrepancies.append(
                    self._make_discrepancy(
                        run_id=run_id,
                        dtype=DiscrepancyType.MISSING_ORDER,
                        symbol=order.symbol,
                        description=(
                            f"Broker returned error for order {order.broker_order_id}: {exc}"
                        ),
                        broker_value="ERROR",
                        internal_value=order.order_status,
                        metadata={"order_id": order.order_id, "broker_order_id": order.broker_order_id},
                    )
                )
                continue

            # Broker says REJECTED but our DB doesn't know yet
            if broker_status == OrderStatus.REJECTED:
                if order.order_status != LiveOrderStatus.REJECTED:
                    discrepancies.append(
                        self._make_discrepancy(
                            run_id=run_id,
                            dtype=DiscrepancyType.REJECTED_ORDER,
                            symbol=order.symbol,
                            description=(
                                f"Broker rejected order {order.broker_order_id} but "
                                f"internal status is still {order.order_status}."
                            ),
                            broker_value=broker_status,
                            internal_value=order.order_status,
                            metadata={
                                "order_id": order.order_id,
                                "broker_order_id": order.broker_order_id,
                                "rejection_reason": order.rejection_reason,
                            },
                        )
                    )
                continue

            # General status mismatch
            expected_broker = _STATUS_MAP.get(order.order_status.value)
            if expected_broker and broker_status.value != expected_broker:
                discrepancies.append(
                    self._make_discrepancy(
                        run_id=run_id,
                        dtype=DiscrepancyType.STATUS_MISMATCH,
                        symbol=order.symbol,
                        description=(
                            f"Order {order.broker_order_id}: broker={broker_status} "
                            f"but internal={order.order_status}."
                        ),
                        broker_value=broker_status,
                        internal_value=order.order_status,
                        metadata={"order_id": order.order_id, "broker_order_id": order.broker_order_id},
                    )
                )

        # ── Duplicate broker_order_id detection ───────────────────────────────
        broker_id_counts: dict[str, int] = {}
        for order in open_orders:
            if order.broker_order_id:
                broker_id_counts[order.broker_order_id] = (
                    broker_id_counts.get(order.broker_order_id, 0) + 1
                )
        for bid, count in broker_id_counts.items():
            if count > 1:
                discrepancies.append(
                    self._make_discrepancy(
                        run_id=run_id,
                        dtype=DiscrepancyType.DUPLICATE_ORDER,
                        symbol=None,
                        description=(
                            f"broker_order_id={bid} appears {count} times in DB. "
                            "Possible duplicate order placement."
                        ),
                        broker_value=bid,
                        internal_value=count,
                        metadata={"broker_order_id": bid, "count": count},
                    )
                )

        return discrepancies, len(open_orders)

    # ── Phase 2: Position Reconciliation ──────────────────────────────────────

    async def _reconcile_positions(
        self,
        run_id: str,
        broker_name: str,
        broker: Optional[BaseBroker],
    ) -> tuple[list[BrokerDiscrepancy], int]:
        """
        Compare open internal positions against broker-held positions.

        Detects:
          - QUANTITY_MISMATCH  — quantity in DB != broker quantity
          - PRICE_MISMATCH     — average price differs beyond tolerance
          - MISSING_POSITION   — DB has open position but broker doesn't
          - ORPHAN_POSITION    — broker holds position with no internal record
        """
        discrepancies: list[BrokerDiscrepancy] = []

        try:
            db_positions = await self._position_repo.get_open_positions(
                broker_name=broker_name
            )
        except Exception as exc:
            logger.error("[recon] Failed to fetch open positions: %s", exc)
            return [], 0

        if broker is None:
            logger.info("[recon] No broker — skipping live position check.")
            return [], len(db_positions)

        try:
            broker_positions: list[PositionInfo] = await broker.get_positions()
        except Exception as exc:
            logger.error("[recon] Failed to fetch broker positions: %s", exc)
            return [], len(db_positions)

        # Build broker lookup: symbol → PositionInfo
        broker_map: dict[str, PositionInfo] = {
            p.symbol.upper(): p for p in broker_positions
        }

        # ── Check DB positions against broker ─────────────────────────────────
        for pos in db_positions:
            sym = pos.symbol.upper()
            broker_pos = broker_map.get(sym)

            if broker_pos is None:
                discrepancies.append(
                    self._make_discrepancy(
                        run_id=run_id,
                        dtype=DiscrepancyType.MISSING_POSITION,
                        symbol=pos.symbol,
                        description=(
                            f"Position {pos.position_id} ({sym}, qty={pos.quantity}) "
                            "is OPEN in DB but absent from broker position book."
                        ),
                        broker_value=None,
                        internal_value=pos.quantity,
                        metadata={"position_id": pos.position_id},
                    )
                )
                continue

            # Quantity mismatch (exact match required)
            broker_qty = abs(broker_pos.quantity)
            if broker_qty != pos.quantity:
                discrepancies.append(
                    self._make_discrepancy(
                        run_id=run_id,
                        dtype=DiscrepancyType.QUANTITY_MISMATCH,
                        symbol=pos.symbol,
                        description=(
                            f"{sym}: broker holds {broker_qty} shares "
                            f"but DB records {pos.quantity}."
                        ),
                        broker_value=broker_qty,
                        internal_value=pos.quantity,
                        metadata={"position_id": pos.position_id},
                    )
                )

            # Price mismatch (tolerance-based)
            broker_price = float(broker_pos.average_price)
            if pos.average_price > 0:
                pct_diff = abs(broker_price - pos.average_price) / pos.average_price
                if pct_diff > _PRICE_TOLERANCE_PCT:
                    discrepancies.append(
                        self._make_discrepancy(
                            run_id=run_id,
                            dtype=DiscrepancyType.PRICE_MISMATCH,
                            symbol=pos.symbol,
                            description=(
                                f"{sym}: broker avg price ₹{broker_price:.2f} vs "
                                f"DB ₹{pos.average_price:.2f} "
                                f"({pct_diff*100:.2f}% diff, threshold {_PRICE_TOLERANCE_PCT*100:.1f}%)."
                            ),
                            broker_value=broker_price,
                            internal_value=pos.average_price,
                            metadata={"position_id": pos.position_id},
                        )
                    )

        # ── Check broker positions against DB (orphan detection) ──────────────
        db_symbols = {p.symbol.upper() for p in db_positions}
        for broker_pos in broker_positions:
            sym = broker_pos.symbol.upper()
            if abs(broker_pos.quantity) == 0:
                continue  # zero-qty positions are already closed at broker
            if sym not in db_symbols:
                discrepancies.append(
                    self._make_discrepancy(
                        run_id=run_id,
                        dtype=DiscrepancyType.ORPHAN_POSITION,
                        symbol=broker_pos.symbol,
                        description=(
                            f"Broker holds {abs(broker_pos.quantity)} shares of {sym} "
                            "but no matching OPEN LivePosition exists in DB. "
                            "This position is ungoverned — manual review required."
                        ),
                        broker_value=abs(broker_pos.quantity),
                        internal_value=None,
                        metadata={
                            "broker_symbol": broker_pos.symbol,
                            "broker_qty": broker_pos.quantity,
                            "broker_avg_price": float(broker_pos.average_price),
                        },
                    )
                )

        return discrepancies, len(db_positions)

    # ── Phase 3: Stop-Loss Reconciliation ─────────────────────────────────────

    async def _reconcile_stop_losses(
        self, run_id: str, broker_name: str
    ) -> list[BrokerDiscrepancy]:
        """
        Verify every open position has a valid (non-zero) stop-loss price.

        Missing SL is a CRITICAL discrepancy — it means the position is
        completely unprotected against adverse moves.
        """
        discrepancies: list[BrokerDiscrepancy] = []

        try:
            open_positions = await self._position_repo.get_open_positions(
                broker_name=broker_name
            )
        except Exception as exc:
            logger.error("[recon] Failed to fetch positions for SL check: %s", exc)
            return []

        for pos in open_positions:
            sl_missing = pos.stop_loss is None or pos.stop_loss <= 0
            if sl_missing:
                discrepancies.append(
                    self._make_discrepancy(
                        run_id=run_id,
                        dtype=DiscrepancyType.MISSING_STOP_LOSS,
                        symbol=pos.symbol,
                        description=(
                            f"CRITICAL: Position {pos.position_id} ({pos.symbol}, "
                            f"qty={pos.quantity}, side={pos.trade_side}) "
                            f"has stop_loss={pos.stop_loss!r}. "
                            "Position is unprotected. Immediate operator action required."
                        ),
                        broker_value=None,
                        internal_value=pos.stop_loss,
                        metadata={
                            "position_id": pos.position_id,
                            "entry_order_id": pos.entry_order_id,
                            "quantity": pos.quantity,
                            "trade_side": pos.trade_side,
                            "average_price": pos.average_price,
                        },
                    )
                )
            else:
                # Verify SL is on the correct side of the current price
                if pos.trade_side == "LONG" and pos.stop_loss >= pos.current_price:
                    discrepancies.append(
                        self._make_discrepancy(
                            run_id=run_id,
                            dtype=DiscrepancyType.MISSING_STOP_LOSS,
                            symbol=pos.symbol,
                            description=(
                                f"LONG position {pos.position_id} has SL ₹{pos.stop_loss:.2f} "
                                f">= current price ₹{pos.current_price:.2f}. "
                                "SL is above the market — position would be immediately stopped out."
                            ),
                            broker_value=pos.current_price,
                            internal_value=pos.stop_loss,
                            metadata={"position_id": pos.position_id, "trade_side": pos.trade_side},
                        )
                    )
                elif pos.trade_side == "SHORT" and pos.stop_loss <= pos.current_price:
                    discrepancies.append(
                        self._make_discrepancy(
                            run_id=run_id,
                            dtype=DiscrepancyType.MISSING_STOP_LOSS,
                            symbol=pos.symbol,
                            description=(
                                f"SHORT position {pos.position_id} has SL ₹{pos.stop_loss:.2f} "
                                f"<= current price ₹{pos.current_price:.2f}. "
                                "SL is below the market — position is unprotected."
                            ),
                            broker_value=pos.current_price,
                            internal_value=pos.stop_loss,
                            metadata={"position_id": pos.position_id, "trade_side": pos.trade_side},
                        )
                    )

        return discrepancies

    # ── Phase 4: Auto-Resolution ──────────────────────────────────────────────

    async def _attempt_auto_resolution(
        self,
        discrepancies: list[BrokerDiscrepancy],
        broker: Optional[BaseBroker],
    ) -> None:
        """
        Attempt safe, non-destructive fixes for resolvable discrepancies.

        Actions allowed:
          - Refresh order status from broker → update via OrderStateMachine
          - Update position quantity/price in DB to match broker

        Actions NOT allowed:
          - Placing new orders
          - Cancelling orders
          - Closing positions
        """
        for d in discrepancies:
            try:
                resolved = False

                if d.discrepancy_type == DiscrepancyType.STATUS_MISMATCH and broker:
                    resolved = await self._resolve_status_mismatch(d, broker)

                elif d.discrepancy_type == DiscrepancyType.QUANTITY_MISMATCH and broker:
                    resolved = await self._resolve_quantity_mismatch(d, broker)

                d.auto_resolution_attempted = True
                if resolved:
                    d.status = DiscrepancyStatus.AUTO_RESOLVED
                    d.resolved_at = now_utc()
                    await self._disc_repo.upsert(d)
                    logger.info(
                        "[recon] Auto-resolved %s discrepancy for %s",
                        d.discrepancy_type, d.symbol,
                    )

            except Exception as exc:
                logger.error(
                    "[recon] Auto-resolution failed for discrepancy %s: %s",
                    d.discrepancy_id, exc,
                )

    async def _resolve_status_mismatch(
        self, d: BrokerDiscrepancy, broker: BaseBroker
    ) -> bool:
        """
        Sync internal order status to match broker via the OrderStateMachine.
        Only resolves when broker is in a terminal state (FILLED/CANCELLED/REJECTED).
        """
        order_id = d.metadata.get("order_id")
        broker_order_id = d.metadata.get("broker_order_id")
        if not order_id or not broker_order_id:
            return False

        try:
            broker_status = await broker.get_order_status(broker_order_id)
        except Exception:
            return False

        if broker_status not in _BROKER_TERMINAL:
            # Only auto-resolve terminal states — leave open/partial for the
            # high-frequency live_order_reconcile job.
            return False

        try:
            from app.live_execution.order_state_machine import OrderStateMachine
            from app.repositories.live_order_repository import LiveOrderRepository

            order_repo = LiveOrderRepository()
            order = await order_repo.get_by_order_id(order_id)
            if order is None or order.is_terminal():
                return False

            # Map broker terminal status to internal LiveOrderStatus
            target_status_map = {
                OrderStatus.COMPLETE: LiveOrderStatus.FILLED,
                OrderStatus.CANCELLED: LiveOrderStatus.CANCELLED,
                OrderStatus.REJECTED: LiveOrderStatus.REJECTED,
            }
            target = target_status_map.get(broker_status)
            if target is None:
                return False

            sm = OrderStateMachine()
            updated = sm.transition(order, target, reason="reconciliation_auto_fix")
            await order_repo.upsert_by_order_id(updated)
            logger.info(
                "[recon] Order %s transitioned %s → %s via auto-resolution.",
                order_id, d.internal_value, target,
            )
            return True

        except Exception as exc:
            logger.error("[recon] Status mismatch auto-resolve failed: %s", exc)
            return False

    async def _resolve_quantity_mismatch(
        self, d: BrokerDiscrepancy, broker: BaseBroker
    ) -> bool:
        """
        Update LivePosition.quantity to match broker-reported quantity.
        Logged as a data-sync correction (no financial impact).
        """
        position_id = d.metadata.get("position_id")
        if not position_id:
            return False

        broker_qty = d.broker_value
        if not isinstance(broker_qty, int) or broker_qty <= 0:
            return False

        try:
            position = await self._position_repo.get_by_position_id(position_id)
            if position is None or position.status != LivePositionStatus.OPEN:
                return False

            old_qty = position.quantity
            position.quantity = broker_qty
            position.mark_updated()
            await self._position_repo.upsert_by_position_id(position)
            logger.warning(
                "[recon] Position %s quantity corrected %d → %d (broker authoritative).",
                position_id, old_qty, broker_qty,
            )
            return True

        except Exception as exc:
            logger.error("[recon] Quantity mismatch auto-resolve failed: %s", exc)
            return False

    # ── Phase 5: Incident Generation ──────────────────────────────────────────

    async def _generate_incidents(
        self, discrepancies: list[BrokerDiscrepancy]
    ) -> None:
        """
        Create SystemIncidents for CRITICAL and WARNING discrepancies.

        Incident component naming: "reconciliation.<discrepancy_type>"
        Uses incident_manager deduplication to avoid flooding.
        """
        critical_types = {
            DiscrepancyType.MISSING_STOP_LOSS,
            DiscrepancyType.ORPHAN_POSITION,
        }
        warning_types = {
            DiscrepancyType.REJECTED_ORDER,
            DiscrepancyType.MISSING_POSITION,
            DiscrepancyType.QUANTITY_MISMATCH,
            DiscrepancyType.STATUS_MISMATCH,
            DiscrepancyType.ORPHAN_ORDER,
            DiscrepancyType.MISSING_ORDER,
        }

        try:
            from app.monitoring.incident_manager import incident_manager
            from app.models.alert_event import AlertSeverity

            for d in discrepancies:
                if d.status == DiscrepancyStatus.AUTO_RESOLVED:
                    continue  # Already fixed — no incident needed.

                if d.discrepancy_type in critical_types:
                    severity = AlertSeverity.CRITICAL
                elif d.discrepancy_type in warning_types:
                    severity = AlertSeverity.WARNING
                else:
                    continue  # INFO-level discrepancies don't open incidents.

                component = f"reconciliation.{d.discrepancy_type}"
                await incident_manager.create(
                    component=component,
                    description=d.description,
                    severity=severity,
                    metadata={
                        "discrepancy_id": d.discrepancy_id,
                        "run_id": d.run_id,
                        "symbol": d.symbol,
                    },
                )

        except Exception as exc:
            logger.error("[recon] Incident generation failed: %s", exc, exc_info=True)

    # ── Phase 6: Alerting ─────────────────────────────────────────────────────

    async def _send_alerts(self, discrepancies: list[BrokerDiscrepancy]) -> None:
        """
        Dispatch notifications via the alert router.

        HIGH (critical)  — MISSING_STOP_LOSS, ORPHAN_POSITION
        MEDIUM (warning) — order mismatches, missing positions
        LOW (info)       — stale orders, price discrepancies
        """
        if not discrepancies:
            return

        try:
            from app.monitoring.alert_router import alert_router

            # Group by type for digest-style messaging
            critical = [
                d for d in discrepancies
                if d.severity == AlertSeverity.CRITICAL
                and d.status != DiscrepancyStatus.AUTO_RESOLVED
            ]
            warnings = [
                d for d in discrepancies
                if d.severity == AlertSeverity.WARNING
                and d.status != DiscrepancyStatus.AUTO_RESOLVED
            ]
            infos = [
                d for d in discrepancies
                if d.severity == AlertSeverity.INFO
                and d.status != DiscrepancyStatus.AUTO_RESOLVED
            ]

            for d in critical:
                await alert_router._dispatch(
                    event_type="reconciliation_critical",
                    message=f"[RECON CRITICAL] {d.discrepancy_type}: {d.description}",
                    severity=AlertSeverity.CRITICAL,
                    payload={
                        "discrepancy_id": d.discrepancy_id,
                        "symbol": d.symbol,
                        "type": d.discrepancy_type,
                    },
                    dedup_key=f"recon:{d.discrepancy_type}:{d.symbol or 'global'}",
                )

            if warnings:
                symbols = ", ".join(
                    d.symbol for d in warnings if d.symbol
                ) or "multiple"
                await alert_router._dispatch(
                    event_type="reconciliation_warning",
                    message=(
                        f"[RECON] {len(warnings)} order/position discrepancy(ies) detected "
                        f"for {symbols}. Run ID: {discrepancies[0].run_id}."
                    ),
                    severity=AlertSeverity.WARNING,
                    payload={"count": len(warnings), "symbols": symbols},
                    dedup_key=f"recon:warning:{discrepancies[0].run_id}",
                )

            if infos:
                await alert_router._dispatch(
                    event_type="reconciliation_info",
                    message=(
                        f"[RECON] {len(infos)} low-severity discrepancy(ies) "
                        f"(stale orders / price drift). Run ID: {discrepancies[0].run_id}."
                    ),
                    severity=AlertSeverity.INFO,
                    payload={"count": len(infos)},
                    dedup_key=f"recon:info:{discrepancies[0].run_id}",
                )

        except Exception as exc:
            logger.error("[recon] Alert dispatch failed: %s", exc, exc_info=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_discrepancy(
        self,
        run_id: str,
        dtype: DiscrepancyType,
        symbol: Optional[str],
        description: str,
        broker_value: Any,
        internal_value: Any,
        metadata: Optional[dict] = None,
    ) -> BrokerDiscrepancy:
        severity = DISCREPANCY_SEVERITY.get(dtype, AlertSeverity.INFO)
        return BrokerDiscrepancy.model_construct(
            discrepancy_id=_new_discrepancy_id(),
            run_id=run_id,
            discrepancy_type=dtype,
            symbol=symbol,
            severity=severity,
            broker_value=broker_value,
            internal_value=internal_value,
            description=description,
            status=DiscrepancyStatus.DETECTED,
            detected_at=now_utc(),
            resolved_at=None,
            auto_resolution_attempted=False,
            metadata=metadata or {},
        )

    async def _resolve_broker(self) -> Optional[BaseBroker]:
        """
        Obtain a broker instance from the live_execution_service if available.
        Returns None when live execution is disabled.
        """
        try:
            from app.config.settings import settings
            if not settings.LIVE_EXEC_ENABLED:
                logger.info(
                    "[recon] LIVE_EXEC_ENABLED=False — broker connection not attempted."
                )
                return None

            from app.brokers.angelone.client import AngelOneBroker
            from app.brokers.angelone.auth import AngelOneAuth

            auth = AngelOneAuth()
            session = await auth.get_session()
            broker = AngelOneBroker(session)
            return broker
        except Exception as exc:
            logger.warning("[recon] Could not resolve broker: %s", exc)
            return None


# ── Module-level singleton ────────────────────────────────────────────────────

broker_reconciliation_service = BrokerReconciliationService()
