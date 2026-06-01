"""
Real-time candle builder.

Aggregates live ticks into 1-minute, 5-minute and 15-minute OHLCV candles.
Designed for low-latency, memory-efficient operation across a large watchlist.

Design properties:
  - Async-safe — a per-symbol lock guards mutations of the in-progress bucket
    so concurrent ticks (or reconnection-driven replays) never corrupt state.
  - Memory efficient — only the in-progress bucket per (symbol, interval) is
    held; closed candles are emitted via callback and dropped from memory.
  - Reconnect-aware — gaps trigger automatic bucket roll-over to the new
    tick's interval slot; partial buckets are flushed on close() or session end.
  - Market-hours aware — ticks outside the regular NSE session are ignored.

Boundary semantics:
  - A candle covers the half-open interval [bucket_start, bucket_start + step).
  - The first 15-min bucket of a session covers [09:15, 09:30) IST exactly,
    which is the canonical ORB candle.

Strategy/integration contract:
  - The builder is pure: it owns no DB, broker, or WebSocket references.
  - Listeners subscribe by registering an async callback receiving
    `BuiltCandle` whenever a candle closes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from app.utils.candle_intervals import CandleInterval
from app.utils.logger import get_logger
from app.utils.market_time import IST, MARKET_CLOSE_TIME, MARKET_OPEN_TIME, to_ist, to_utc

logger = get_logger(__name__)

# Intervals tracked per symbol. Order is significant — finer intervals are
# computed first so consumers can rely on consistent emission order.
DEFAULT_INTERVALS: tuple[CandleInterval, ...] = (
    CandleInterval.ONE_MINUTE,
    CandleInterval.FIVE_MINUTE,
    CandleInterval.FIFTEEN_MINUTE,
)

_INTERVAL_STEP: dict[CandleInterval, timedelta] = {
    CandleInterval.ONE_MINUTE: timedelta(minutes=1),
    CandleInterval.FIVE_MINUTE: timedelta(minutes=5),
    CandleInterval.FIFTEEN_MINUTE: timedelta(minutes=15),
    CandleInterval.THIRTY_MINUTE: timedelta(minutes=30),
    CandleInterval.ONE_HOUR: timedelta(hours=1),
}


# ── Public dataclasses ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Tick:
    """A single market data tick (price + volume delta)."""

    symbol: str
    price: float
    volume: int
    timestamp: datetime  # tz-aware


@dataclass(frozen=True)
class BuiltCandle:
    """A completed OHLCV candle emitted by the builder."""

    symbol: str
    interval: CandleInterval
    start_time: datetime  # UTC, candle open
    end_time: datetime    # UTC, candle close (exclusive boundary)
    open: float
    high: float
    low: float
    close: float
    volume: int

    @property
    def range_percent(self) -> float:
        """(high - low) / low * 100. Returns 0 if low <= 0."""
        if self.low <= 0:
            return 0.0
        return (self.high - self.low) / self.low * 100


CandleCallback = Callable[[BuiltCandle], Awaitable[None]]


# ── Internal in-progress bucket ──────────────────────────────────────────────

@dataclass
class _Bucket:
    """Mutable in-progress aggregation for one (symbol, interval) slot."""

    start_time: datetime           # UTC, candle start
    end_time: datetime             # UTC, candle end (exclusive)
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    tick_count: int = 0

    def update(self, price: float, volume: int) -> None:
        if price > self.high:
            self.high = price
        if price < self.low:
            self.low = price
        self.close = price
        self.volume += volume
        self.tick_count += 1


# ── Builder ──────────────────────────────────────────────────────────────────

class CandleBuilder:
    """
    Aggregates ticks into 1m / 5m / 15m candles for many symbols.

    Concurrency model:
      - One asyncio.Lock per symbol (lazy-created). Mutations to a symbol's
        buckets and callback dispatch are serialised through this lock.
      - Locks are independent per symbol, so the builder scales linearly
        with the watchlist without cross-symbol contention.
    """

    def __init__(
        self,
        intervals: tuple[CandleInterval, ...] = DEFAULT_INTERVALS,
        respect_market_hours: bool = True,
    ) -> None:
        unsupported = [i for i in intervals if i not in _INTERVAL_STEP]
        if unsupported:
            raise ValueError(f"Unsupported intervals: {unsupported!r}")
        self._intervals: tuple[CandleInterval, ...] = intervals
        self._respect_market_hours: bool = respect_market_hours

        # symbol -> interval -> bucket | None
        self._buckets: dict[str, dict[CandleInterval, Optional[_Bucket]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._callbacks: list[CandleCallback] = []

        # Counters (debug / observability)
        self._ticks_processed: int = 0
        self._ticks_dropped: int = 0
        self._candles_emitted: int = 0

    # ── Subscription API ──────────────────────────────────────────────────────

    def on_candle(self, callback: CandleCallback) -> None:
        """Register an async callback fired on every closed candle."""
        self._callbacks.append(callback)

    # ── Symbol lifecycle ──────────────────────────────────────────────────────

    def register_symbol(self, symbol: str) -> None:
        """Pre-register a symbol so the lock/bucket dicts exist before ticks arrive."""
        symbol = symbol.upper()
        if symbol not in self._buckets:
            self._buckets[symbol] = {i: None for i in self._intervals}
            self._locks[symbol] = asyncio.Lock()

    def unregister_symbol(self, symbol: str) -> None:
        """Remove all in-memory state for a symbol."""
        symbol = symbol.upper()
        self._buckets.pop(symbol, None)
        self._locks.pop(symbol, None)

    # ── Tick ingestion ────────────────────────────────────────────────────────

    async def on_tick(self, tick: Tick) -> list[BuiltCandle]:
        """
        Process one live tick. Returns the list of candles that closed
        as a result of this tick (typically 0 or 1; can be > 1 on bucket
        boundary alignment across intervals, e.g. 09:30 closes 1m+5m+15m).

        Ticks are dropped (not raised) on any sanity failure:
          - naive (timezone-less) timestamp
          - non-positive price or volume
          - timestamp outside NSE regular session when respect_market_hours=True
          - timestamp far in the future (clock skew protection)
        Dropping rather than raising keeps a noisy feed from crashing the
        engine; counters expose drops via the health monitor.
        """
        if tick.timestamp.tzinfo is None:
            self._ticks_dropped += 1
            return []

        # Sanity: price must be positive and finite; volume non-negative.
        # Negative or zero prices are typically broker placeholders for
        # unsubscribed symbols and would otherwise corrupt OHLC.
        if not (tick.price > 0) or tick.volume < 0:
            self._ticks_dropped += 1
            return []

        # Clock-skew guard — accept up to 60s of "future" for clock drift,
        # drop anything further to keep the bucket grid sane.
        now = datetime.now(timezone.utc)
        if tick.timestamp > now + timedelta(seconds=60):
            self._ticks_dropped += 1
            return []

        if self._respect_market_hours and not _within_market_hours(tick.timestamp):
            self._ticks_dropped += 1
            return []

        symbol = tick.symbol.upper()
        # Lazy registration — safe for callers that don't pre-register.
        if symbol not in self._buckets:
            self.register_symbol(symbol)

        lock = self._locks[symbol]
        emitted: list[BuiltCandle] = []
        async with lock:
            ts_utc = to_utc(tick.timestamp)

            for interval in self._intervals:
                step = _INTERVAL_STEP[interval]
                bucket = self._buckets[symbol][interval]

                # If a bucket exists and this tick belongs to a later bucket,
                # close out all elapsed buckets up to (but not including) the
                # one that contains the tick. This makes the builder robust
                # to gaps in the tick stream (reconnects, slow feeds).
                if bucket is not None and ts_utc >= bucket.end_time:
                    emitted.append(self._finalise(symbol, interval, bucket))
                    self._buckets[symbol][interval] = None
                    bucket = None

                if bucket is None:
                    start = _floor_to_interval(ts_utc, step)
                    bucket = _Bucket(
                        start_time=start,
                        end_time=start + step,
                        open=tick.price,
                        high=tick.price,
                        low=tick.price,
                        close=tick.price,
                    )
                    self._buckets[symbol][interval] = bucket

                bucket.update(tick.price, tick.volume)

            self._ticks_processed += 1

        # Dispatch callbacks outside the lock to avoid holding it across
        # potentially slow consumers.
        for candle in emitted:
            await self._dispatch(candle)

        return emitted

    # ── Manual flushing ───────────────────────────────────────────────────────

    async def flush_symbol(self, symbol: str) -> list[BuiltCandle]:
        """
        Force-close all in-progress buckets for a symbol.

        Used at market close / session reset to surface partial candles.
        """
        symbol = symbol.upper()
        if symbol not in self._buckets:
            return []
        emitted: list[BuiltCandle] = []
        async with self._locks[symbol]:
            for interval, bucket in list(self._buckets[symbol].items()):
                if bucket is None:
                    continue
                emitted.append(self._finalise(symbol, interval, bucket))
                self._buckets[symbol][interval] = None
        for candle in emitted:
            await self._dispatch(candle)
        return emitted

    async def flush_all(self) -> list[BuiltCandle]:
        """Flush every registered symbol. Returns all emitted candles."""
        all_emitted: list[BuiltCandle] = []
        for symbol in list(self._buckets.keys()):
            all_emitted.extend(await self.flush_symbol(symbol))
        return all_emitted

    def reset(self) -> None:
        """
        Drop ALL in-progress state without emitting.

        Used by the market session engine at session start (next trading day)
        so yesterday's stale buckets cannot leak into today's analysis.
        """
        self._buckets.clear()
        # Locks are intentionally preserved — they are cheap and re-creating
        # them mid-event-loop would race with anyone holding a reference.

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def registered_symbols(self) -> list[str]:
        return list(self._buckets.keys())

    @property
    def stats(self) -> dict:
        return {
            "ticks_processed": self._ticks_processed,
            "ticks_dropped": self._ticks_dropped,
            "candles_emitted": self._candles_emitted,
            "symbols": len(self._buckets),
        }

    def get_in_progress(
        self, symbol: str, interval: CandleInterval
    ) -> Optional[BuiltCandle]:
        """Return a snapshot of the in-progress bucket (read-only) or None."""
        bucket = self._buckets.get(symbol.upper(), {}).get(interval)
        if bucket is None:
            return None
        return BuiltCandle(
            symbol=symbol.upper(),
            interval=interval,
            start_time=bucket.start_time,
            end_time=bucket.end_time,
            open=bucket.open,
            high=bucket.high,
            low=bucket.low,
            close=bucket.close,
            volume=bucket.volume,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _finalise(
        self, symbol: str, interval: CandleInterval, bucket: _Bucket
    ) -> BuiltCandle:
        self._candles_emitted += 1
        return BuiltCandle(
            symbol=symbol,
            interval=interval,
            start_time=bucket.start_time,
            end_time=bucket.end_time,
            open=bucket.open,
            high=bucket.high,
            low=bucket.low,
            close=bucket.close,
            volume=bucket.volume,
        )

    async def _dispatch(self, candle: BuiltCandle) -> None:
        for cb in self._callbacks:
            try:
                await cb(candle)
            except Exception as exc:
                # Never let a misbehaving listener kill the builder.
                logger.error(
                    "Candle callback raised for %s %s: %s",
                    candle.symbol, candle.interval, exc, exc_info=True,
                )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _floor_to_interval(dt: datetime, step: timedelta) -> datetime:
    """
    Floor a UTC-aware datetime to the start of its interval slot, aligned to
    the IST trading day's 09:15 open. The alignment ensures the 15-min slots
    are exactly [09:15, 09:30, 09:45, …] in IST — matching the historical
    candle convention used by the strategy engine.
    """
    dt_ist = to_ist(dt)
    day_open = IST.localize(
        datetime(
            dt_ist.year, dt_ist.month, dt_ist.day,
            MARKET_OPEN_TIME.hour, MARKET_OPEN_TIME.minute,
        )
    )
    if dt_ist < day_open:
        # Tick before open — bucket to the previous day's open + step grid.
        prior = day_open - timedelta(days=1)
        delta = dt_ist - prior
    else:
        delta = dt_ist - day_open
    step_seconds = int(step.total_seconds())
    floored_seconds = (int(delta.total_seconds()) // step_seconds) * step_seconds
    start_ist = (day_open if dt_ist >= day_open else day_open - timedelta(days=1)) \
        + timedelta(seconds=floored_seconds)
    return start_ist.astimezone(timezone.utc)


def _within_market_hours(dt: datetime) -> bool:
    """Return True if dt (tz-aware) is inside NSE regular session hours."""
    dt_ist = to_ist(dt)
    if dt_ist.weekday() >= 5:
        return False
    t = dt_ist.time()
    return MARKET_OPEN_TIME <= t < MARKET_CLOSE_TIME
