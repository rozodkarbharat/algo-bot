"""
Unit tests for the live CandleBuilder.

Pure asyncio tests — no DB, no broker. Each test seeds ticks at known IST
times and asserts the builder's emitted candles + in-progress snapshots.

Run with:
    pytest tests/test_candle_builder.py -v
"""

from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from app.live.candle_builder import BuiltCandle, CandleBuilder, Tick
from app.utils.candle_intervals import CandleInterval

IST = pytz.timezone("Asia/Kolkata")


def _ist(hour: int, minute: int, second: int = 0, date: tuple = (2024, 1, 15)) -> datetime:
    """Build an IST-aware datetime for the given date and HH:MM:SS."""
    y, m, d = date
    return IST.localize(datetime(y, m, d, hour, minute, second))


def _make_recorder() -> tuple[list[BuiltCandle], "callable"]:
    """Return (sink_list, async_callback) for capturing emitted candles."""
    sink: list[BuiltCandle] = []

    async def cb(candle: BuiltCandle) -> None:
        sink.append(candle)

    return sink, cb


@pytest.fixture
def builder() -> CandleBuilder:
    return CandleBuilder()


# ── Bucketing ────────────────────────────────────────────────────────────────

class TestBucketing:
    @pytest.mark.asyncio
    async def test_first_15m_candle_anchors_to_0915(self, builder: CandleBuilder) -> None:
        """Ticks between 09:15 and 09:30 land in the 09:15 15-min bucket."""
        ticks = [
            Tick("RELIANCE", price=100.0, volume=10, timestamp=_ist(9, 15, 5)),
            Tick("RELIANCE", price=101.5, volume=15, timestamp=_ist(9, 22, 0)),
            Tick("RELIANCE", price=99.5, volume=5, timestamp=_ist(9, 29, 59)),
        ]
        for t in ticks:
            await builder.on_tick(t)

        snap = builder.get_in_progress("RELIANCE", CandleInterval.FIFTEEN_MINUTE)
        assert snap is not None
        assert snap.open == 100.0
        assert snap.high == 101.5
        assert snap.low == 99.5
        assert snap.close == 99.5
        assert snap.volume == 30
        # Bucket should anchor to 09:15 IST = 03:45 UTC
        assert snap.start_time.hour == 3 and snap.start_time.minute == 45

    @pytest.mark.asyncio
    async def test_candle_emitted_when_next_bucket_starts(
        self, builder: CandleBuilder
    ) -> None:
        """A tick in the next interval finalises and emits the current bucket."""
        emitted, cb = _make_recorder()
        builder.on_candle(cb)

        await builder.on_tick(Tick("RELIANCE", 100, 10, _ist(9, 15, 0)))
        await builder.on_tick(Tick("RELIANCE", 101, 10, _ist(9, 29, 59)))
        # Tick at 09:30 — closes the 09:15 candle for all intervals.
        await builder.on_tick(Tick("RELIANCE", 102, 5, _ist(9, 30, 0)))

        emitted_intervals = {c.interval for c in emitted if c.symbol == "RELIANCE"}
        assert CandleInterval.ONE_MINUTE in emitted_intervals
        assert CandleInterval.FIVE_MINUTE in emitted_intervals
        assert CandleInterval.FIFTEEN_MINUTE in emitted_intervals

        fifteen = next(c for c in emitted if c.interval == CandleInterval.FIFTEEN_MINUTE)
        assert fifteen.open == 100
        assert fifteen.high == 101
        assert fifteen.low == 100
        assert fifteen.close == 101  # close = last tick within the bucket
        assert fifteen.volume == 20

    @pytest.mark.asyncio
    async def test_range_percent(self, builder: CandleBuilder) -> None:
        await builder.on_tick(Tick("X", 100.0, 1, _ist(9, 15)))
        await builder.on_tick(Tick("X", 101.0, 1, _ist(9, 20)))
        snap = builder.get_in_progress("X", CandleInterval.FIFTEEN_MINUTE)
        assert snap is not None
        assert snap.range_percent == pytest.approx(1.0)


# ── Reconnect / gap handling ─────────────────────────────────────────────────

class TestReconnects:
    @pytest.mark.asyncio
    async def test_gap_advances_to_correct_bucket(self, builder: CandleBuilder) -> None:
        """A long gap between ticks should not corrupt the new bucket."""
        emitted, cb = _make_recorder()
        builder.on_candle(cb)

        await builder.on_tick(Tick("X", 100, 1, _ist(9, 15)))
        # Simulate 1 hour of dropped feed; resume at 10:15.
        await builder.on_tick(Tick("X", 110, 5, _ist(10, 15)))

        # The 09:15 15-min bucket should have closed because 10:15 ≥ its end.
        fifteens = [c for c in emitted if c.interval == CandleInterval.FIFTEEN_MINUTE]
        assert any(c.start_time.hour == 3 and c.start_time.minute == 45 for c in fifteens)

        snap = builder.get_in_progress("X", CandleInterval.FIFTEEN_MINUTE)
        assert snap is not None
        # 10:15 IST = 04:45 UTC
        assert snap.start_time.hour == 4 and snap.start_time.minute == 45
        assert snap.open == 110

    @pytest.mark.asyncio
    async def test_flush_all_emits_partial(self, builder: CandleBuilder) -> None:
        emitted, cb = _make_recorder()
        builder.on_candle(cb)
        await builder.on_tick(Tick("X", 100, 1, _ist(9, 17)))
        flushed = await builder.flush_all()
        assert flushed
        assert all(c.open == 100 for c in flushed)
        assert all(c.close == 100 for c in flushed)


# ── Market hours guard ───────────────────────────────────────────────────────

class TestMarketHours:
    @pytest.mark.asyncio
    async def test_pre_open_ticks_dropped(self, builder: CandleBuilder) -> None:
        await builder.on_tick(Tick("X", 100, 1, _ist(9, 0)))
        assert builder.stats["ticks_dropped"] == 1
        assert builder.stats["ticks_processed"] == 0

    @pytest.mark.asyncio
    async def test_post_close_ticks_dropped(self, builder: CandleBuilder) -> None:
        await builder.on_tick(Tick("X", 100, 1, _ist(15, 31)))
        assert builder.stats["ticks_dropped"] == 1

    @pytest.mark.asyncio
    async def test_weekend_dropped(self, builder: CandleBuilder) -> None:
        # 2024-01-13 = Saturday
        sat = IST.localize(datetime(2024, 1, 13, 10, 0))
        await builder.on_tick(Tick("X", 100, 1, sat))
        assert builder.stats["ticks_dropped"] == 1
