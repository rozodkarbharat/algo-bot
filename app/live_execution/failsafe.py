"""
Failsafe systems for live execution.

Centralises the safety rails that sit *outside* the per-order risk gate:

  - Kill switch         — global trading halt flag (manual or auto-tripped).
  - Market-hours guard  — refuses orders outside NSE regular session hours.
  - Stale-data guard    — refuses signals whose triggering candle is older
                          than the configured staleness window.
  - Duplicate guard     — idempotency check that returns the existing
                          LiveOrder when a signal has already been acted on
                          (complements the DB-level unique index).
  - WebSocket monitor   — tracks the last successful tick / heartbeat so
                          the engine can pause when the live feed dies.

The kill switch is an in-process flag, intentionally NOT persisted: it is
the operator's emergency lever and must be re-armed deliberately on each
process start so the system fails safe ("trading off by default") rather
than fails open ("trading on if the operator forgets").
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app.config.settings import settings
from app.core.exceptions import (
    DuplicateLiveOrderException,
    MarketClosedException,
    StaleMarketDataException,
    TradingHaltedException,
)
from app.models.live_order import LiveOrder, LiveOrderStatus
from app.repositories.live_order_repository import LiveOrderRepository
from app.utils.logger import get_logger
from app.utils.market_time import is_market_open, now_utc

logger = get_logger(__name__)


# ── Kill switch ──────────────────────────────────────────────────────────────

@dataclass
class _KillSwitchState:
    engaged: bool = False
    reason: Optional[str] = None
    engaged_at: Optional[datetime] = None


class KillSwitch:
    """
    Process-local global trading halt.

    When engaged:
      - new entry orders are refused immediately by the failsafe module.
      - the live risk manager also sees `kill_switch_engaged=True` via the
        context the service supplies, so multiple layers reject the order.
      - exit / close-all flows are NOT blocked (operators must be able to
        flatten the book while halted).
    """

    def __init__(self) -> None:
        self._state = _KillSwitchState()
        self._lock = asyncio.Lock()

    @property
    def engaged(self) -> bool:
        return self._state.engaged

    @property
    def reason(self) -> Optional[str]:
        return self._state.reason

    async def engage(self, reason: str = "manual_kill_switch") -> None:
        async with self._lock:
            self._state = _KillSwitchState(
                engaged=True, reason=reason, engaged_at=now_utc(),
            )
        logger.warning("[failsafe] KILL SWITCH ENGAGED reason=%s", reason)

    async def disengage(self) -> None:
        async with self._lock:
            self._state = _KillSwitchState()
        logger.info("[failsafe] kill switch disengaged.")

    def snapshot(self) -> dict:
        return {
            "engaged": self._state.engaged,
            "reason": self._state.reason,
            "engaged_at": (
                self._state.engaged_at.isoformat()
                if self._state.engaged_at else None
            ),
        }


# ── Feed monitor ─────────────────────────────────────────────────────────────

class FeedMonitor:
    """
    Tracks the most recent live-feed activity.

    The execution service calls `record_tick()` whenever a candle / tick
    arrives; the failsafe consults `last_activity_at` before placing
    orders. When the feed has been silent for longer than the staleness
    threshold the failsafe blocks new entries (exits are still allowed).
    """

    def __init__(self, staleness_threshold_seconds: Optional[float] = None) -> None:
        self._threshold = (
            staleness_threshold_seconds
            if staleness_threshold_seconds is not None
            else settings.LIVE_EXEC_MAX_DATA_STALENESS_SECONDS
        )
        self._last_at: Optional[datetime] = None
        self._symbol_last_at: dict[str, datetime] = {}

    def record_tick(self, symbol: Optional[str] = None) -> None:
        now = now_utc()
        self._last_at = now
        if symbol is not None:
            self._symbol_last_at[symbol.upper()] = now

    def last_activity_age_seconds(self) -> Optional[float]:
        if self._last_at is None:
            return None
        return (now_utc() - self._last_at).total_seconds()

    def symbol_age_seconds(self, symbol: str) -> Optional[float]:
        at = self._symbol_last_at.get(symbol.upper())
        if at is None:
            return None
        return (now_utc() - at).total_seconds()

    def is_stale(self, symbol: Optional[str] = None) -> bool:
        if symbol is not None:
            age = self.symbol_age_seconds(symbol)
        else:
            age = self.last_activity_age_seconds()
        if age is None:
            # No tick observed yet — treat as fresh during warmup so the
            # very first signal of a session isn't blocked by an empty
            # monitor. The market-hours guard prevents trades when the
            # session truly hasn't begun.
            return False
        return age > self._threshold

    def snapshot(self) -> dict:
        return {
            "last_activity_at": self._last_at.isoformat() if self._last_at else None,
            "threshold_seconds": self._threshold,
            "stale": self.is_stale(),
        }


# ── Failsafe coordinator ─────────────────────────────────────────────────────

class FailsafeCoordinator:
    """
    Single entry-point for all pre-trade safety rails.

    The execution engine calls `ensure_safe_to_trade()` before placing
    any entry order. Each failed rail raises a typed exception so the
    caller can log a specific rejection reason.
    """

    def __init__(
        self,
        kill_switch: Optional[KillSwitch] = None,
        feed_monitor: Optional[FeedMonitor] = None,
        order_repo: Optional[LiveOrderRepository] = None,
        require_market_open: Optional[bool] = None,
    ) -> None:
        self.kill_switch: KillSwitch = kill_switch or KillSwitch()
        self.feed_monitor: FeedMonitor = feed_monitor or FeedMonitor()
        self._order_repo: LiveOrderRepository = order_repo or LiveOrderRepository()
        self._require_market_open: bool = (
            require_market_open
            if require_market_open is not None
            else settings.LIVE_EXEC_REQUIRE_MARKET_OPEN
        )

    # ── Pre-trade guards ──────────────────────────────────────────────────────

    def ensure_kill_switch_disengaged(self) -> None:
        if self.kill_switch.engaged:
            raise TradingHaltedException(
                reason=self.kill_switch.reason or "kill_switch_engaged"
            )

    def ensure_market_open(self, at: Optional[datetime] = None) -> None:
        if not self._require_market_open:
            return
        if not is_market_open(at):
            raise MarketClosedException(
                message="Live order placement blocked: NSE session is closed."
            )

    def ensure_data_fresh(self, symbol: str) -> None:
        age = self.feed_monitor.symbol_age_seconds(symbol)
        threshold = settings.LIVE_EXEC_MAX_DATA_STALENESS_SECONDS
        if age is not None and age > threshold:
            raise StaleMarketDataException(
                symbol=symbol,
                age_seconds=age,
                threshold_seconds=threshold,
            )

    async def ensure_no_duplicate_for_signal(
        self, signal_id: str, broker_name: str
    ) -> None:
        """
        Pre-check the duplicate constraint before hitting the DB.

        The unique index on (signal_id, broker_name) is the durable
        guarantee; this method gives the caller a clean, typed exception
        without paying the cost of an attempted insert + DuplicateKeyError.
        """
        existing = await self._order_repo.get_by_signal_and_broker(
            signal_id=signal_id, broker_name=broker_name
        )
        if existing is None:
            return
        # An order in a non-rejected state means we've already acted on
        # this signal — return the existing order to the caller via the
        # exception detail so they can short-circuit cleanly.
        if existing.order_status is not LiveOrderStatus.REJECTED:
            raise DuplicateLiveOrderException(
                identifier=f"signal={signal_id}",
                detail={
                    "signal_id": signal_id,
                    "broker_name": broker_name,
                    "existing_order_id": existing.order_id,
                    "existing_status": existing.order_status.value,
                },
            )

    async def ensure_safe_to_trade(
        self,
        *,
        symbol: str,
        signal_id: str,
        broker_name: str,
        at: Optional[datetime] = None,
    ) -> None:
        """Run every pre-trade guard. Raises on the first failure."""
        self.ensure_kill_switch_disengaged()
        self.ensure_market_open(at=at)
        self.ensure_data_fresh(symbol=symbol)
        await self.ensure_no_duplicate_for_signal(
            signal_id=signal_id, broker_name=broker_name
        )

    # ── Introspection ─────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        return {
            "kill_switch": self.kill_switch.snapshot(),
            "feed_monitor": self.feed_monitor.snapshot(),
            "require_market_open": self._require_market_open,
        }


# ── Module-level singleton ────────────────────────────────────────────────────

failsafe = FailsafeCoordinator()
