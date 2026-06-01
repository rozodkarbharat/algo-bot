"""
Order state machine — enforces valid LiveOrder lifecycle transitions.

The state machine is the single authority allowed to mutate
`LiveOrder.order_status`. Every transition:

  1. Validates the requested transition against the allowed-transitions map.
  2. Appends a structured row to `LiveOrder.transitions` (audit log).
  3. Updates `LiveOrder.order_status` and any side-effect fields (e.g.
     `rejection_reason` on REJECTED, `broker_order_id` on OPEN).
  4. Persists the mutated row via the repository (callers can defer
     persistence by passing `persist=False` for batched writes).

Multi-broker readiness:
  - The transitions map is broker-agnostic. New brokers can plug in
    without changing this module — they only need to map their native
    status strings to `LiveOrderStatus` values (see `_STATUS_MAP` in
    `app/brokers/angelone/client.py`).

Concurrency:
  - The class is process-local and not thread-safe; expect the live
    execution engine to call into it from a single asyncio task at a time
    per order. A per-order `asyncio.Lock` (in the engine) suffices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.core.exceptions import InvalidOrderStateTransitionException
from app.models.live_order import LiveOrder, LiveOrderStatus
from app.repositories.live_order_repository import LiveOrderRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Allowed transitions ──────────────────────────────────────────────────────

# Maps `from_state -> {to_states}`. Anything else is a transition violation.
_ALLOWED: dict[LiveOrderStatus, set[LiveOrderStatus]] = {
    LiveOrderStatus.PENDING: {
        LiveOrderStatus.OPEN,
        LiveOrderStatus.REJECTED,
        LiveOrderStatus.CANCELLED,  # cancelled before reaching the book
        # PENDING → FILLED is allowed for market orders that fill instantly
        # before we ever see an OPEN state (e.g. broker returns COMPLETE
        # in the order book on the first poll).
        LiveOrderStatus.FILLED,
        LiveOrderStatus.PARTIALLY_FILLED,
    },
    LiveOrderStatus.OPEN: {
        LiveOrderStatus.PARTIALLY_FILLED,
        LiveOrderStatus.FILLED,
        LiveOrderStatus.CANCELLED,
        LiveOrderStatus.REJECTED,   # rare, but broker can post-reject
    },
    LiveOrderStatus.PARTIALLY_FILLED: {
        LiveOrderStatus.PARTIALLY_FILLED,  # additional fills update qty
        LiveOrderStatus.FILLED,
        LiveOrderStatus.CANCELLED,
    },
    # Terminal states — no further transitions allowed.
    LiveOrderStatus.FILLED: set(),
    LiveOrderStatus.CANCELLED: set(),
    LiveOrderStatus.REJECTED: set(),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Public types ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TransitionResult:
    """Outcome of a state-machine transition attempt."""

    order: LiveOrder
    from_state: LiveOrderStatus
    to_state: LiveOrderStatus
    audit_row: dict


# ── State machine ────────────────────────────────────────────────────────────

class OrderStateMachine:
    """
    Enforces valid order transitions and writes an audit row on every move.

    The state machine is responsible only for the LiveOrder document.
    Updating LivePosition / PaperAccount in response to a FILLED order
    is the orchestrating service's job — keeping this layer pure makes
    it trivially testable.
    """

    def __init__(self, repo: Optional[LiveOrderRepository] = None) -> None:
        self._repo: LiveOrderRepository = repo or LiveOrderRepository()

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def is_valid_transition(
        from_state: LiveOrderStatus, to_state: LiveOrderStatus
    ) -> bool:
        """True iff `from_state -> to_state` is permitted by the state map."""
        return to_state in _ALLOWED.get(from_state, set())

    async def transition(
        self,
        order: LiveOrder,
        to_state: LiveOrderStatus,
        *,
        reason: Optional[str] = None,
        broker_order_id: Optional[str] = None,
        executed_price: Optional[float] = None,
        filled_quantity: Optional[int] = None,
        rejection_reason: Optional[str] = None,
        metadata: Optional[dict] = None,
        persist: bool = True,
    ) -> TransitionResult:
        """
        Move `order` to `to_state`, validating the transition and recording
        an audit row in `order.transitions`.

        Args:
            order: the LiveOrder being transitioned (mutated in place).
            to_state: target status.
            reason: free-form reason recorded on the audit row.
            broker_order_id: populated alongside the first OPEN transition.
            executed_price: VWAP from the broker; populated on fills.
            filled_quantity: cumulative filled quantity; populated on fills.
            rejection_reason: required for REJECTED transitions.
            metadata: optional extra payload to merge into the audit row.
            persist: when True (default), upserts the order via the repo.

        Raises:
            InvalidOrderStateTransitionException — if the transition is
            not allowed by the state map.
        """
        from_state = order.order_status
        if not self.is_valid_transition(from_state, to_state):
            logger.error(
                "[order-sm] invalid transition %s → %s for order %s",
                from_state.value, to_state.value, order.order_id,
            )
            raise InvalidOrderStateTransitionException(
                from_state=from_state.value,
                to_state=to_state.value,
                order_id=order.order_id,
            )

        # Mutate the order with the target state + any side-effect fields.
        order.order_status = to_state
        if broker_order_id is not None and not order.broker_order_id:
            order.broker_order_id = broker_order_id
        if executed_price is not None:
            order.executed_price = executed_price
        if filled_quantity is not None:
            order.filled_quantity = max(order.filled_quantity, filled_quantity)
        if to_state is LiveOrderStatus.REJECTED:
            order.rejection_reason = (
                rejection_reason or reason or "rejected_by_broker"
            )
        order.mark_updated()

        # Audit row — keeps full traceability without a separate collection.
        audit_row: dict = {
            "from": from_state.value,
            "to": to_state.value,
            "at": _utcnow().isoformat(),
            "reason": reason,
        }
        if broker_order_id is not None:
            audit_row["broker_order_id"] = broker_order_id
        if executed_price is not None:
            audit_row["executed_price"] = executed_price
        if filled_quantity is not None:
            audit_row["filled_quantity"] = filled_quantity
        if rejection_reason is not None:
            audit_row["rejection_reason"] = rejection_reason
        if metadata:
            audit_row["metadata"] = metadata
        order.transitions.append(audit_row)

        if persist:
            await self._repo.upsert_by_order_id(order)

        logger.info(
            "[order-sm] %s → %s order=%s broker_id=%s reason=%s",
            from_state.value, to_state.value, order.order_id,
            order.broker_order_id, reason,
        )
        return TransitionResult(
            order=order,
            from_state=from_state,
            to_state=to_state,
            audit_row=audit_row,
        )
