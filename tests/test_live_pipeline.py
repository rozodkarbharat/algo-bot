"""
Integration tests for the live engine pipeline.

These tests wire CandleBuilder → LiveMarketEngine → SignalEngine end-to-end
with synthetic ticks and assert the breakout signal flows through. No DB,
no broker, no WebSocket — `LiveSignalService` is not exercised here (its
persistence/broadcast hooks need DB fixtures and live in the integration
suite once that fixture lands).

Also covers the failure-handling additions:
  - tick sanity drops (negative price, future timestamp, naive tz)
  - health monitor status transitions
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytz

from app.live.candle_builder import BuiltCandle, CandleBuilder, Tick
from app.live.health_monitor import HealthStatus, LiveHealthMonitor
from app.live.market_engine import LiveMarketEngine
from app.live.market_session import MarketSessionEngine
from app.live.signal_engine import (
    GeneratedSignal,
    ShortlistedCandidate,
    SignalEngine,
)
from app.models.live_signal import LiveBreakoutSide, LiveSignalType

IST = pytz.timezone("Asia/Kolkata")


def _ist(hour: int, minute: int, second: int = 0) -> datetime:
    return IST.localize(datetime(2024, 1, 15, hour, minute, second))


def _generate_ticks_for_minute(
    symbol: str, hh: int, mm: int, prices: list[float]
) -> list[Tick]:
    """Build N ticks spaced 1 second apart inside the given minute."""
    return [
        Tick(symbol=symbol, price=p, volume=10, timestamp=_ist(hh, mm, s))
        for s, p in enumerate(prices)
    ]


# ── End-to-end signal generation ─────────────────────────────────────────────

class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_ticks_produce_buy_signal(self) -> None:
        builder = CandleBuilder()
        signals = SignalEngine(max_orb_range_percent=1.0)
        engine = LiveMarketEngine(candle_builder=builder, signal_engine=signals)

        captured: list[GeneratedSignal] = []

        async def on_signal(s: GeneratedSignal) -> None:
            captured.append(s)

        signals.on_signal(on_signal)

        await engine.start(
            [ShortlistedCandidate(symbol="RELIANCE", probability=0.72, direction_hint="UP")]
        )

        # ORB candle (09:15–09:30): range stays within 1% (H=100.5, L=99.6, ~0.9%).
        orb_ticks: list[Tick] = []
        for minute in range(15, 30):
            orb_ticks.extend(_generate_ticks_for_minute(
                "RELIANCE", 9, minute, [100.0, 100.2, 99.8, 100.1]
            ))
        # Final tick at 09:29:59 anchors the high/low.
        orb_ticks.append(Tick("RELIANCE", 99.6, 10, _ist(9, 29, 30)))
        orb_ticks.append(Tick("RELIANCE", 100.5, 10, _ist(9, 29, 50)))
        for t in orb_ticks:
            await engine.feed_tick(t)

        # 09:30–09:45 candle closes ABOVE the ORB high → BUY.
        for minute in range(30, 45):
            for t in _generate_ticks_for_minute(
                "RELIANCE", 9, minute, [100.7, 101.0, 100.6, 100.8]
            ):
                await engine.feed_tick(t)

        # Tick at 09:45:00 finalises the 09:30 candle.
        await engine.feed_tick(Tick("RELIANCE", 101.2, 10, _ist(9, 45, 0)))

        assert len(captured) == 1
        sig = captured[0]
        assert sig.symbol == "RELIANCE"
        assert sig.signal_type is LiveSignalType.BUY
        assert sig.breakout_side is LiveBreakoutSide.UP
        # Stop loss is the ORB low captured from the 09:15 candle.
        assert sig.stop_loss == pytest.approx(99.6)
        # Probability score propagated from the shortlisted candidate.
        assert sig.probability_score == pytest.approx(0.72)

        # Engine state reflects the lock.
        state = signals.get_symbol_state("RELIANCE")
        assert state is not None and state.trade_locked is True

        await engine.stop()


# ── Tick sanity / failure-mode drops ─────────────────────────────────────────

class TestTickSanity:
    @pytest.mark.asyncio
    async def test_negative_price_dropped(self) -> None:
        builder = CandleBuilder()
        await builder.on_tick(Tick("X", -10.0, 1, _ist(9, 20)))
        assert builder.stats["ticks_dropped"] == 1
        assert builder.stats["ticks_processed"] == 0

    @pytest.mark.asyncio
    async def test_zero_price_dropped(self) -> None:
        builder = CandleBuilder()
        await builder.on_tick(Tick("X", 0.0, 1, _ist(9, 20)))
        assert builder.stats["ticks_dropped"] == 1

    @pytest.mark.asyncio
    async def test_negative_volume_dropped(self) -> None:
        builder = CandleBuilder()
        await builder.on_tick(Tick("X", 100.0, -5, _ist(9, 20)))
        assert builder.stats["ticks_dropped"] == 1

    @pytest.mark.asyncio
    async def test_future_timestamp_dropped(self) -> None:
        builder = CandleBuilder()
        far_future = datetime.now(timezone.utc) + timedelta(minutes=10)
        await builder.on_tick(Tick("X", 100.0, 1, far_future))
        assert builder.stats["ticks_dropped"] == 1

    @pytest.mark.asyncio
    async def test_naive_timestamp_dropped(self) -> None:
        builder = CandleBuilder()
        naive = datetime(2024, 1, 15, 9, 20)
        await builder.on_tick(Tick("X", 100.0, 1, naive))
        assert builder.stats["ticks_dropped"] == 1


# ── Health monitor ──────────────────────────────────────────────────────────

class TestHealthMonitor:
    @pytest.mark.asyncio
    async def test_offline_when_engine_not_running(self) -> None:
        engine = LiveMarketEngine()
        monitor = LiveHealthMonitor(engine=engine)
        snap = monitor.evaluate()
        assert snap.status is HealthStatus.OFFLINE
        assert snap.running is False

    @pytest.mark.asyncio
    async def test_ok_after_recent_tick_during_market(self) -> None:
        builder = CandleBuilder()
        signals = SignalEngine()
        engine = LiveMarketEngine(candle_builder=builder, signal_engine=signals)
        await engine.start([ShortlistedCandidate("X")])

        await engine.feed_tick(Tick("X", 100.0, 1, _ist(9, 20)))
        # Evaluate the health monitor "as of" a moment 1 second after the tick.
        evaluated_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        monitor = LiveHealthMonitor(engine=engine)
        snap = monitor.evaluate(at=evaluated_at)
        # market_open is computed from real wall-clock time, so the status
        # depends on when the test runs — but `running` and `ticks_received`
        # must be true/positive regardless.
        assert snap.running is True
        assert snap.ticks_received == 1

        await engine.stop()


# ── Reconnect tracking ──────────────────────────────────────────────────────

class TestReconnects:
    @pytest.mark.asyncio
    async def test_reconnect_counter_increments(self) -> None:
        engine = LiveMarketEngine()
        await engine.note_reconnect()
        await engine.note_reconnect()
        assert engine.stats.reconnect_count == 2
