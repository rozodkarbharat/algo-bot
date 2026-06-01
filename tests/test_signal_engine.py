"""
Unit tests for the live SignalEngine.

Pure asyncio tests — feeds synthetic BuiltCandle events directly into the
engine and asserts:
  - First-candle (ORB) capture
  - ORB range filter (skip when > max)
  - BUY signal on close above ORB high
  - SELL signal on close below ORB low
  - Time filter (entry only between 09:30 IST and 11:30 IST)
  - One trade per stock per day (no duplicate signals)
  - Look-ahead safety: signals only on closed candles
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
import pytz

from app.live.candle_builder import BuiltCandle
from app.live.signal_engine import GeneratedSignal, ShortlistedCandidate, SignalEngine
from app.models.live_signal import LiveBreakoutSide, LiveSignalType
from app.utils.candle_intervals import CandleInterval

IST = pytz.timezone("Asia/Kolkata")


def _utc(hour: int, minute: int, date_: tuple = (2024, 1, 15)) -> datetime:
    """Convert IST HH:MM to a UTC datetime."""
    y, m, d = date_
    return IST.localize(datetime(y, m, d, hour, minute)).astimezone(timezone.utc)


def _candle(
    symbol: str,
    open_ist_hh: int,
    open_ist_mm: int,
    o: float,
    h: float,
    l: float,
    c: float,
    interval: CandleInterval = CandleInterval.FIFTEEN_MINUTE,
    volume: int = 1000,
) -> BuiltCandle:
    start = _utc(open_ist_hh, open_ist_mm)
    return BuiltCandle(
        symbol=symbol,
        interval=interval,
        start_time=start,
        end_time=start + timedelta(minutes=15),
        open=o, high=h, low=l, close=c, volume=volume,
    )


@pytest.fixture
def engine() -> SignalEngine:
    e = SignalEngine(max_orb_range_percent=1.0)
    e.activate(
        trading_date=date(2024, 1, 15),
        shortlist=[
            ShortlistedCandidate(symbol="RELIANCE", probability=0.7, direction_hint="UP"),
        ],
    )
    return e


def _collector() -> tuple[list[GeneratedSignal], "callable"]:
    sink: list[GeneratedSignal] = []

    async def cb(s: GeneratedSignal) -> None:
        sink.append(s)

    return sink, cb


# ── First-candle capture ─────────────────────────────────────────────────────

class TestFirstCandleCapture:
    @pytest.mark.asyncio
    async def test_first_candle_captured(self, engine: SignalEngine) -> None:
        orb = _candle("RELIANCE", 9, 15, 100, 100.5, 99.6, 100.2)
        result = await engine.on_candle(orb)
        assert result is None  # no breakout yet
        state = engine.get_symbol_state("RELIANCE")
        assert state is not None
        assert state.first_candle is not None
        assert state.orb_high == 100.5
        assert state.orb_low == 99.6

    @pytest.mark.asyncio
    async def test_orb_range_filter_skips_wide_candle(self, engine: SignalEngine) -> None:
        """ORB > 1% → marked as skipped and no signals can be generated."""
        # Range = (101 - 99) / 99 * 100 ≈ 2.02%
        orb = _candle("RELIANCE", 9, 15, 100, 101, 99, 100)
        await engine.on_candle(orb)
        state = engine.get_symbol_state("RELIANCE")
        assert state is not None
        assert state.orb_skipped_reason is not None
        # Subsequent breakouts must NOT emit anything.
        sink, cb = _collector()
        engine.on_signal(cb)
        breakout = _candle("RELIANCE", 9, 30, 100, 102, 100, 102)
        emitted = await engine.on_candle(breakout)
        assert emitted is None
        assert sink == []


# ── Breakout detection ───────────────────────────────────────────────────────

class TestBreakoutDetection:
    @pytest.mark.asyncio
    async def test_buy_signal_on_close_above_orb_high(self, engine: SignalEngine) -> None:
        sink, cb = _collector()
        engine.on_signal(cb)

        orb = _candle("RELIANCE", 9, 15, 100, 100.5, 99.6, 100.2)
        await engine.on_candle(orb)

        # Close above ORB high → BUY
        bo = _candle("RELIANCE", 9, 30, 100.2, 101, 100.1, 100.8)
        signal = await engine.on_candle(bo)
        assert signal is not None
        assert signal.signal_type is LiveSignalType.BUY
        assert signal.breakout_side is LiveBreakoutSide.UP
        assert signal.entry_price == 100.8
        assert signal.stop_loss == 99.6  # ORB low
        assert sink and sink[0].signal_type is LiveSignalType.BUY

    @pytest.mark.asyncio
    async def test_sell_signal_on_close_below_orb_low(self, engine: SignalEngine) -> None:
        sink, cb = _collector()
        engine.on_signal(cb)

        orb = _candle("RELIANCE", 9, 15, 100, 100.5, 99.6, 100.2)
        await engine.on_candle(orb)

        # Close below ORB low → SELL
        bo = _candle("RELIANCE", 9, 30, 100.2, 100.4, 99.0, 99.5)
        signal = await engine.on_candle(bo)
        assert signal is not None
        assert signal.signal_type is LiveSignalType.SELL
        assert signal.breakout_side is LiveBreakoutSide.DOWN
        assert signal.stop_loss == 100.5  # ORB high

    @pytest.mark.asyncio
    async def test_no_signal_when_close_inside_orb(self, engine: SignalEngine) -> None:
        """High pokes above ORB but close is inside → no signal (look-ahead safe)."""
        orb = _candle("RELIANCE", 9, 15, 100, 100.5, 99.6, 100.2)
        await engine.on_candle(orb)
        bo = _candle("RELIANCE", 9, 30, 100.2, 100.8, 100.1, 100.3)  # high > ORB, close < ORB high
        signal = await engine.on_candle(bo)
        assert signal is None


# ── Time filter ──────────────────────────────────────────────────────────────

class TestTimeFilter:
    @pytest.mark.asyncio
    async def test_signal_at_lower_boundary(self, engine: SignalEngine) -> None:
        """09:30 candle (the first eligible breakout) should be allowed."""
        orb = _candle("RELIANCE", 9, 15, 100, 100.5, 99.6, 100.2)
        await engine.on_candle(orb)
        bo = _candle("RELIANCE", 9, 30, 100.2, 101, 100.1, 100.8)
        signal = await engine.on_candle(bo)
        assert signal is not None

    @pytest.mark.asyncio
    async def test_no_signal_after_1130(self, engine: SignalEngine) -> None:
        """Breakout candle opening at 11:30 IST is OUTSIDE the entry window (exclusive)."""
        orb = _candle("RELIANCE", 9, 15, 100, 100.5, 99.6, 100.2)
        await engine.on_candle(orb)
        bo = _candle("RELIANCE", 11, 30, 100.2, 102, 100, 101.5)
        signal = await engine.on_candle(bo)
        assert signal is None

    @pytest.mark.asyncio
    async def test_signal_at_1115(self, engine: SignalEngine) -> None:
        """11:15 candle is inside the entry window."""
        orb = _candle("RELIANCE", 9, 15, 100, 100.5, 99.6, 100.2)
        await engine.on_candle(orb)
        bo = _candle("RELIANCE", 11, 15, 100.2, 101, 100.1, 100.8)
        signal = await engine.on_candle(bo)
        assert signal is not None


# ── Duplicate prevention ─────────────────────────────────────────────────────

class TestDuplicateSuppression:
    @pytest.mark.asyncio
    async def test_one_trade_per_symbol_per_day(self, engine: SignalEngine) -> None:
        orb = _candle("RELIANCE", 9, 15, 100, 100.5, 99.6, 100.2)
        await engine.on_candle(orb)

        first = _candle("RELIANCE", 9, 30, 100.2, 101, 100.1, 100.8)
        bo1 = await engine.on_candle(first)
        assert bo1 is not None

        # Another breakout in the next candle should NOT emit a second signal.
        second = _candle("RELIANCE", 9, 45, 100.8, 102, 100.7, 101.5)
        bo2 = await engine.on_candle(second)
        assert bo2 is None

    @pytest.mark.asyncio
    async def test_lock_symbol_blocks_further_signals(self, engine: SignalEngine) -> None:
        orb = _candle("RELIANCE", 9, 15, 100, 100.5, 99.6, 100.2)
        await engine.on_candle(orb)
        # External lock (e.g. db duplicate suppression) before any breakout.
        engine.lock_symbol("RELIANCE")
        bo = _candle("RELIANCE", 9, 30, 100.2, 101, 100.1, 100.8)
        assert await engine.on_candle(bo) is None


# ── Non-shortlisted symbols / inactive engine ────────────────────────────────

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_inactive_engine_no_op(self) -> None:
        e = SignalEngine()
        bo = _candle("X", 9, 30, 100, 101, 100, 100.8)
        assert await e.on_candle(bo) is None

    @pytest.mark.asyncio
    async def test_non_shortlisted_symbol_ignored(self, engine: SignalEngine) -> None:
        bo = _candle("INFY", 9, 30, 100, 101, 100, 100.8)
        assert await engine.on_candle(bo) is None

    @pytest.mark.asyncio
    async def test_non_15m_interval_ignored(self, engine: SignalEngine) -> None:
        bo = _candle(
            "RELIANCE", 9, 30, 100, 101, 100, 100.8,
            interval=CandleInterval.ONE_MINUTE,
        )
        assert await engine.on_candle(bo) is None
