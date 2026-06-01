"""
Unit tests for the live position manager.

Mirrors test_paper_position_manager.py: SL detection (LONG + SHORT),
EOD-exit, MTM updates, halt and EOD sweeps, and broker reconciliation
diffs.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest
import pytz

from app.live.candle_builder import BuiltCandle
from app.live_execution.live_position_manager import LivePositionManager
from app.models.live_order import LiveTradeSide
from app.models.live_position import (
    LiveExitReason,
    LivePosition,
    LivePositionStatus,
)
from app.utils.candle_intervals import CandleInterval

IST = pytz.timezone("Asia/Kolkata")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _candle(
    symbol: str,
    open_ist_hh: int,
    open_ist_mm: int,
    close: float,
    high: float | None = None,
    low: float | None = None,
) -> BuiltCandle:
    start = IST.localize(datetime(2024, 6, 3, open_ist_hh, open_ist_mm)).astimezone(timezone.utc)
    return BuiltCandle(
        symbol=symbol,
        interval=CandleInterval.FIFTEEN_MINUTE,
        start_time=start,
        end_time=start + timedelta(minutes=15),
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=1000,
    )


def _position(
    symbol: str = "RELIANCE",
    side: LiveTradeSide = LiveTradeSide.LONG,
    quantity: int = 10,
    entry: float = 2500.0,
    sl: float = 2475.0,
    current: float | None = None,
) -> LivePosition:
    return LivePosition.model_construct(
        position_id=f"pos-{symbol}-{side.value}",
        broker_name="AngelOne",
        signal_id="sig-1",
        entry_order_id="ord-1",
        exit_order_id=None,
        symbol=symbol,
        exchange="NSE",
        trading_date=datetime(2024, 6, 3, tzinfo=timezone.utc),
        trade_side=side,
        quantity=quantity,
        average_price=entry,
        current_price=current if current is not None else entry,
        stop_loss=sl,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        status=LivePositionStatus.OPEN,
        exit_reason=None,
        exit_price=None,
        opened_at=datetime(2024, 6, 3, 9, 45, tzinfo=timezone.utc),
        closed_at=None,
        updated_at=datetime(2024, 6, 3, 9, 45, tzinfo=timezone.utc),
        metadata={},
    )


def _mgr(eod: time = time(15, 15)) -> LivePositionManager:
    return LivePositionManager(eod_exit_time_ist=eod)


# ── Tests ────────────────────────────────────────────────────────────────────

class TestBookLifecycle:
    @pytest.mark.asyncio
    async def test_add_and_remove(self) -> None:
        m = _mgr()
        pos = _position()
        await m.add_position(pos)
        assert m.open_count == 1
        assert m.has_open_for_symbol("RELIANCE")
        removed = await m.remove_position(pos.position_id)
        assert removed is pos
        assert m.open_count == 0
        assert not m.has_open_for_symbol("RELIANCE")

    @pytest.mark.asyncio
    async def test_hydrate_replaces_book(self) -> None:
        m = _mgr()
        await m.add_position(_position("TCS"))
        await m.hydrate([_position("HDFC", entry=1500.0)])
        assert m.open_count == 1
        assert m.has_open_for_symbol("HDFC")
        assert not m.has_open_for_symbol("TCS")


class TestMarkToMarket:
    @pytest.mark.asyncio
    async def test_long_mtm_positive(self) -> None:
        m = _mgr()
        pos = _position(side=LiveTradeSide.LONG, entry=2500.0, sl=2475.0)
        await m.add_position(pos)
        updates, exits = await m.on_candle(_candle("RELIANCE", 10, 0, close=2520.0))
        assert len(updates) == 1
        assert exits == []
        assert pos.current_price == 2520.0
        assert pos.unrealized_pnl == 200.0  # (2520-2500)*10

    @pytest.mark.asyncio
    async def test_short_mtm_positive_on_price_drop(self) -> None:
        m = _mgr()
        pos = _position(side=LiveTradeSide.SHORT, entry=2500.0, sl=2525.0)
        await m.add_position(pos)
        updates, exits = await m.on_candle(_candle("RELIANCE", 10, 0, close=2480.0))
        assert len(updates) == 1
        assert exits == []
        assert pos.unrealized_pnl == 200.0  # (2500-2480)*10


class TestSLDetection:
    @pytest.mark.asyncio
    async def test_long_sl_hit(self) -> None:
        m = _mgr()
        pos = _position(side=LiveTradeSide.LONG, entry=2500.0, sl=2475.0)
        await m.add_position(pos)
        _, exits = await m.on_candle(_candle("RELIANCE", 10, 0, close=2470.0))
        assert len(exits) == 1
        assert exits[0].exit_reason is LiveExitReason.SL_HIT

    @pytest.mark.asyncio
    async def test_short_sl_hit(self) -> None:
        m = _mgr()
        pos = _position(side=LiveTradeSide.SHORT, entry=2500.0, sl=2525.0)
        await m.add_position(pos)
        _, exits = await m.on_candle(_candle("RELIANCE", 10, 0, close=2530.0))
        assert len(exits) == 1
        assert exits[0].exit_reason is LiveExitReason.SL_HIT


class TestEODExit:
    @pytest.mark.asyncio
    async def test_eod_candle_triggers_exit(self) -> None:
        m = _mgr(eod=time(15, 15))
        pos = _position()
        await m.add_position(pos)
        _, exits = await m.on_candle(_candle("RELIANCE", 15, 15, close=2510.0))
        assert len(exits) == 1
        assert exits[0].exit_reason is LiveExitReason.EOD_EXIT

    @pytest.mark.asyncio
    async def test_collect_eod_exits_stamps_all(self) -> None:
        m = _mgr()
        for sym in ("RELIANCE", "TCS"):
            await m.add_position(_position(sym))
        now = datetime(2024, 6, 3, 9, 45, tzinfo=timezone.utc)
        exits = await m.collect_eod_exits(now)
        assert len(exits) == 2
        assert all(d.exit_reason is LiveExitReason.EOD_EXIT for d in exits)

    @pytest.mark.asyncio
    async def test_collect_halt_exits_stamps_risk_halt(self) -> None:
        m = _mgr()
        await m.add_position(_position("INFY"))
        exits = await m.collect_halt_exits(datetime(2024, 6, 3, 11, 0, tzinfo=timezone.utc))
        assert len(exits) == 1
        assert exits[0].exit_reason is LiveExitReason.RISK_HALT


class TestExposure:
    @pytest.mark.asyncio
    async def test_total_exposure_sum(self) -> None:
        m = _mgr()
        await m.add_position(_position("RELIANCE", quantity=10, entry=2500.0))
        await m.add_position(_position("TCS", quantity=5, entry=3000.0))
        # 10*2500 + 5*3000 = 25_000 + 15_000 = 40_000
        assert m.total_exposure() == 40_000.0


class TestReconciliation:
    @pytest.mark.asyncio
    async def test_qty_mismatch_diff(self) -> None:
        m = _mgr()
        await m.add_position(_position("RELIANCE", quantity=10, side=LiveTradeSide.LONG))
        diffs = m.reconcile_with_broker(
            {"RELIANCE": {"quantity": 5, "average_price": 2500.0}}
        )
        assert len(diffs) == 1
        assert diffs[0].kind == "qty_mismatch"

    @pytest.mark.asyncio
    async def test_engine_only_diff(self) -> None:
        m = _mgr()
        await m.add_position(_position("RELIANCE", quantity=10))
        diffs = m.reconcile_with_broker({})
        assert len(diffs) == 1
        assert diffs[0].kind == "engine_only"

    @pytest.mark.asyncio
    async def test_broker_only_diff(self) -> None:
        m = _mgr()
        diffs = m.reconcile_with_broker(
            {"INFY": {"quantity": 5, "average_price": 1500.0}}
        )
        assert len(diffs) == 1
        assert diffs[0].kind == "broker_only"

    @pytest.mark.asyncio
    async def test_clean_book_no_diffs(self) -> None:
        m = _mgr()
        await m.add_position(_position("RELIANCE", quantity=10, side=LiveTradeSide.LONG))
        diffs = m.reconcile_with_broker(
            {"RELIANCE": {"quantity": 10, "average_price": 2500.0}}
        )
        assert diffs == []
