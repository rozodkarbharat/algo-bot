"""
Unit tests for the paper position manager.

Tests SL detection (LONG + SHORT, close-based), EOD-exit detection,
mark-to-market updates, and the helper exit-collection paths.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest
import pytz

from app.live.candle_builder import BuiltCandle
from app.models.paper_position import PaperPosition, PaperPositionStatus, PaperTradeSide
from app.models.paper_trade import PaperExitReason
from app.paper_trading.position_manager import PaperPositionManager
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
    h = high if high is not None else max(close, close)
    l = low if low is not None else min(close, close)
    return BuiltCandle(
        symbol=symbol,
        interval=CandleInterval.FIFTEEN_MINUTE,
        start_time=start,
        end_time=start + timedelta(minutes=15),
        open=close,
        high=h,
        low=l,
        close=close,
        volume=1000,
    )


_counter = {"n": 0}


def _next_pid() -> str:
    _counter["n"] += 1
    return f"pos-{_counter['n']}"


def _long_position(entry: float = 100.0, sl: float = 98.0, qty: int = 10) -> PaperPosition:
    now = datetime(2024, 6, 3, 4, 0, 0, tzinfo=timezone.utc)
    return PaperPosition.model_construct(
        position_id=_next_pid(),
        symbol="RELIANCE",
        trading_date=datetime(2024, 6, 3, tzinfo=timezone.utc),
        trade_side=PaperTradeSide.LONG,
        quantity=qty,
        entry_price=entry,
        current_price=entry,
        stop_loss=sl,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        status=PaperPositionStatus.OPEN,
        signal_id=None,
        opened_at=now,
        closed_at=None,
        metadata={},
        updated_at=now,
    )


def _short_position(entry: float = 100.0, sl: float = 102.0, qty: int = 10) -> PaperPosition:
    now = datetime(2024, 6, 3, 4, 0, 0, tzinfo=timezone.utc)
    return PaperPosition.model_construct(
        position_id=_next_pid(),
        symbol="RELIANCE",
        trading_date=datetime(2024, 6, 3, tzinfo=timezone.utc),
        trade_side=PaperTradeSide.SHORT,
        quantity=qty,
        entry_price=entry,
        current_price=entry,
        stop_loss=sl,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        status=PaperPositionStatus.OPEN,
        signal_id=None,
        opened_at=now,
        closed_at=None,
        metadata={},
        updated_at=now,
    )


# ── MTM updates ──────────────────────────────────────────────────────────────

class TestMarkToMarket:
    @pytest.mark.asyncio
    async def test_long_unrealized_pnl_updates(self) -> None:
        pm = PaperPositionManager(eod_exit_time_ist=time(15, 15))
        await pm.add_position(_long_position(entry=100.0, sl=98.0, qty=10))
        updates, exits = await pm.on_candle(_candle("RELIANCE", 10, 0, close=105.0))
        assert exits == []
        assert len(updates) == 1
        position = updates[0].position
        assert position.current_price == 105.0
        assert position.unrealized_pnl == pytest.approx(50.0)


# ── SL detection ─────────────────────────────────────────────────────────────

class TestStopLoss:
    @pytest.mark.asyncio
    async def test_long_sl_hit_when_close_at_or_below(self) -> None:
        pm = PaperPositionManager(eod_exit_time_ist=time(15, 15))
        await pm.add_position(_long_position(entry=100.0, sl=98.0))
        _, exits = await pm.on_candle(_candle("RELIANCE", 10, 0, close=98.0))
        assert len(exits) == 1
        assert exits[0].exit_reason is PaperExitReason.SL_HIT
        assert exits[0].exit_reference_price == 98.0

    @pytest.mark.asyncio
    async def test_long_sl_not_hit_above_sl(self) -> None:
        pm = PaperPositionManager(eod_exit_time_ist=time(15, 15))
        await pm.add_position(_long_position(entry=100.0, sl=98.0))
        _, exits = await pm.on_candle(_candle("RELIANCE", 10, 0, close=98.5))
        assert exits == []

    @pytest.mark.asyncio
    async def test_short_sl_hit_when_close_at_or_above(self) -> None:
        pm = PaperPositionManager(eod_exit_time_ist=time(15, 15))
        await pm.add_position(_short_position(entry=100.0, sl=102.0))
        _, exits = await pm.on_candle(_candle("RELIANCE", 10, 0, close=102.0))
        assert len(exits) == 1
        assert exits[0].exit_reason is PaperExitReason.SL_HIT


# ── EOD exit ─────────────────────────────────────────────────────────────────

class TestEODExit:
    @pytest.mark.asyncio
    async def test_eod_exit_on_15_15_candle(self) -> None:
        pm = PaperPositionManager(eod_exit_time_ist=time(15, 15))
        await pm.add_position(_long_position(entry=100.0, sl=98.0))
        # Candle opens at 15:00 IST, closes at 15:15 → end_time = 15:15 = EOD.
        _, exits = await pm.on_candle(_candle("RELIANCE", 15, 0, close=101.0))
        assert len(exits) == 1
        assert exits[0].exit_reason is PaperExitReason.EOD_EXIT

    @pytest.mark.asyncio
    async def test_pre_eod_candle_no_exit(self) -> None:
        pm = PaperPositionManager(eod_exit_time_ist=time(15, 15))
        await pm.add_position(_long_position(entry=100.0, sl=98.0))
        _, exits = await pm.on_candle(_candle("RELIANCE", 14, 0, close=101.0))
        assert exits == []

    @pytest.mark.asyncio
    async def test_sl_takes_priority_over_eod(self) -> None:
        """If SL is hit on the EOD bar, exit_reason is SL_HIT (more conservative reporting)."""
        pm = PaperPositionManager(eod_exit_time_ist=time(15, 15))
        await pm.add_position(_long_position(entry=100.0, sl=98.0))
        _, exits = await pm.on_candle(_candle("RELIANCE", 15, 0, close=97.0))
        assert len(exits) == 1
        assert exits[0].exit_reason is PaperExitReason.SL_HIT


# ── Halt / collect helpers ───────────────────────────────────────────────────

class TestCollectHelpers:
    @pytest.mark.asyncio
    async def test_collect_halt_exits_returns_all_open(self) -> None:
        pm = PaperPositionManager(eod_exit_time_ist=time(15, 15))
        await pm.add_position(_long_position())
        p2 = _short_position()
        p2.symbol = "TCS"
        await pm.add_position(p2)
        exits = await pm.collect_halt_exits(now_dt_utc=datetime.now(timezone.utc))
        assert len(exits) == 2
        assert {e.exit_reason for e in exits} == {PaperExitReason.RISK_HALT}

    @pytest.mark.asyncio
    async def test_remove_position_drops_from_book(self) -> None:
        pm = PaperPositionManager(eod_exit_time_ist=time(15, 15))
        p = _long_position()
        await pm.add_position(p)
        assert pm.open_count == 1
        removed = await pm.remove_position(p.position_id)
        assert removed is not None
        assert pm.open_count == 0
        assert pm.get_position(p.position_id) is None

    @pytest.mark.asyncio
    async def test_has_open_for_symbol_prevents_duplicates(self) -> None:
        pm = PaperPositionManager(eod_exit_time_ist=time(15, 15))
        await pm.add_position(_long_position())
        assert pm.has_open_for_symbol("RELIANCE") is True
        assert pm.has_open_for_symbol("INFY") is False


# ── Ignore unrelated symbols ─────────────────────────────────────────────────

class TestSymbolIsolation:
    @pytest.mark.asyncio
    async def test_unrelated_symbol_candle_is_ignored(self) -> None:
        pm = PaperPositionManager(eod_exit_time_ist=time(15, 15))
        await pm.add_position(_long_position())
        updates, exits = await pm.on_candle(_candle("INFY", 10, 0, close=99.0))
        assert updates == [] and exits == []
