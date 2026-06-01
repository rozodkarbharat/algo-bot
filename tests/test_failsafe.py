"""
Unit tests for the failsafe coordinator (kill switch + feed monitor +
duplicate guard + market hours).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from app.core.exceptions import (
    DuplicateLiveOrderException,
    MarketClosedException,
    StaleMarketDataException,
    TradingHaltedException,
)
from app.live_execution.failsafe import (
    FailsafeCoordinator,
    FeedMonitor,
    KillSwitch,
)
from app.models.live_order import LiveOrder, LiveOrderStatus, LiveOrderType, LiveTradeSide
from app.utils.market_time import date_to_utc_midnight, now_utc


class _FakeOrderRepo:
    def __init__(self, existing: Optional[LiveOrder] = None) -> None:
        self._existing = existing

    async def get_by_signal_and_broker(
        self, signal_id: str, broker_name: str
    ) -> Optional[LiveOrder]:
        return self._existing


def _existing_order(status: LiveOrderStatus = LiveOrderStatus.OPEN) -> LiveOrder:
    return LiveOrder.model_construct(
        order_id="existing-1",
        broker_order_id="BR-1",
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


# ── Kill switch ──────────────────────────────────────────────────────────────

class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_engage_and_disengage(self) -> None:
        ks = KillSwitch()
        assert ks.engaged is False
        await ks.engage(reason="test")
        assert ks.engaged is True
        assert ks.reason == "test"
        await ks.disengage()
        assert ks.engaged is False
        assert ks.reason is None


# ── Feed monitor ─────────────────────────────────────────────────────────────

class TestFeedMonitor:
    def test_no_ticks_is_not_stale(self) -> None:
        m = FeedMonitor(staleness_threshold_seconds=10.0)
        assert m.is_stale() is False

    def test_recent_tick_is_fresh(self) -> None:
        m = FeedMonitor(staleness_threshold_seconds=60.0)
        m.record_tick("RELIANCE")
        assert m.is_stale("RELIANCE") is False

    def test_stale_after_threshold(self) -> None:
        m = FeedMonitor(staleness_threshold_seconds=0.001)
        m.record_tick("RELIANCE")
        # Backdate the tick to simulate an old observation.
        m._symbol_last_at["RELIANCE"] = (
            now_utc() - timedelta(seconds=10)
        )
        assert m.is_stale("RELIANCE") is True


# ── Coordinator guards ───────────────────────────────────────────────────────

def _coord(
    require_market_open: bool = False,
    existing_order: Optional[LiveOrder] = None,
) -> FailsafeCoordinator:
    return FailsafeCoordinator(
        kill_switch=KillSwitch(),
        feed_monitor=FeedMonitor(staleness_threshold_seconds=60.0),
        order_repo=_FakeOrderRepo(existing=existing_order),  # type: ignore[arg-type]
        require_market_open=require_market_open,
    )


class TestKillSwitchGuard:
    @pytest.mark.asyncio
    async def test_engaged_raises(self) -> None:
        c = _coord()
        await c.kill_switch.engage(reason="halt-for-test")
        with pytest.raises(TradingHaltedException):
            c.ensure_kill_switch_disengaged()


class TestMarketHoursGuard:
    def test_disabled_does_not_raise(self) -> None:
        c = _coord(require_market_open=False)
        # Saturday — market is closed.
        c.ensure_market_open(at=datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc))

    def test_enabled_raises_outside_session(self) -> None:
        c = _coord(require_market_open=True)
        with pytest.raises(MarketClosedException):
            # Saturday — never open.
            c.ensure_market_open(at=datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc))


class TestStaleDataGuard:
    def test_no_tick_does_not_raise(self) -> None:
        c = _coord()
        c.ensure_data_fresh("RELIANCE")

    def test_stale_tick_raises(self) -> None:
        c = _coord()
        c.feed_monitor.record_tick("RELIANCE")
        c.feed_monitor._symbol_last_at["RELIANCE"] = (
            now_utc() - timedelta(hours=1)
        )
        with pytest.raises(StaleMarketDataException):
            c.ensure_data_fresh("RELIANCE")


class TestDuplicateGuard:
    @pytest.mark.asyncio
    async def test_no_existing_passes(self) -> None:
        c = _coord(existing_order=None)
        await c.ensure_no_duplicate_for_signal("sig-1", "AngelOne")

    @pytest.mark.asyncio
    async def test_existing_open_order_raises(self) -> None:
        c = _coord(existing_order=_existing_order(LiveOrderStatus.OPEN))
        with pytest.raises(DuplicateLiveOrderException):
            await c.ensure_no_duplicate_for_signal("sig-1", "AngelOne")

    @pytest.mark.asyncio
    async def test_existing_rejected_order_does_not_block(self) -> None:
        # If a prior attempt was rejected, a fresh placement should be allowed.
        c = _coord(existing_order=_existing_order(LiveOrderStatus.REJECTED))
        await c.ensure_no_duplicate_for_signal("sig-1", "AngelOne")
