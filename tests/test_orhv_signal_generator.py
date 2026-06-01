"""
Tests for ORHVSignalGenerator (Phase 3 — Live Signal Generation).

All tests use pure in-memory data — zero DB or I/O dependencies.
"""

import pytest
from datetime import date, datetime, timezone

from app.models.historical_candle import CandleData
from app.strategy.strategies.opening_range_historical_validation.config import ORHVConfig
from app.strategy.strategies.opening_range_historical_validation.signal_generator import (
    ORHVCandidate,
    ORHVSignalGenerator,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

TRADING_DATE = date(2024, 3, 15)
CANDIDATE_DATE = date(2024, 3, 14)


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2024, 3, 15, hour, minute, tzinfo=timezone.utc)


def _candle_with_symbol(
    symbol: str,
    h: float, l: float, c: float,
    hour: int, minute: int = 0,
) -> CandleData:
    cd = CandleData(open=c * 0.99, high=h, low=l, close=c, volume=5000,
                    time=_utc(hour, minute))
    object.__setattr__(cd, "symbol", symbol) if not hasattr(cd, "symbol") else None
    cd.__dict__["symbol"] = symbol  # inject symbol for signal generator
    return cd


def _make_candidate(symbol: str = "RELIANCE") -> ORHVCandidate:
    return ORHVCandidate(
        symbol=symbol,
        win_rate=0.75,
        occurrences_used=28,
        candidate_date=CANDIDATE_DATE,
        orh_d=110.0,
        orl_d=100.0,
    )


@pytest.fixture
def gen():
    cfg = ORHVConfig(max_orb_range_pct=1.0)
    g = ORHVSignalGenerator(cfg)
    g.activate(TRADING_DATE, [_make_candidate()])
    return g


def _orb(h=105.0, l=104.0, close_=104.5) -> CandleData:
    """Opening Range candle at 9:15 IST (03:45 UTC)."""
    cd = CandleData(open=104.0, high=h, low=l, close=close_, volume=10000,
                    time=_utc(3, 45))
    cd.__dict__["symbol"] = "RELIANCE"
    return cd


# ── Initialization ────────────────────────────────────────────────────────────

def test_engine_activates_correctly():
    gen = ORHVSignalGenerator()
    gen.activate(TRADING_DATE, [_make_candidate()])
    assert gen.active
    assert gen.stats["candidates"] == 1


def test_inactive_engine_returns_none():
    gen = ORHVSignalGenerator()
    # Not activated
    cd = _orb()
    result = gen.on_candle(cd)
    assert result is None


def test_unknown_symbol_ignored(gen):
    cd = _orb()
    cd.__dict__["symbol"] = "UNKNOWN"
    result = gen.on_candle(cd)
    assert result is None


# ── ORB capture ───────────────────────────────────────────────────────────────

def test_orb_candle_captured_no_signal(gen):
    """First candle (ORB) should be captured but not generate a signal."""
    cd = _orb()
    result = gen.on_candle(cd)
    assert result is None
    assert gen._states["RELIANCE"].first_candle is not None


def test_range_filter_rejects_wide_orb():
    """ORB range > 1% should mark range_rejected=True and produce no signals."""
    cfg = ORHVConfig(max_orb_range_pct=1.0)
    gen = ORHVSignalGenerator(cfg)
    gen.activate(TRADING_DATE, [_make_candidate()])

    # Wide ORB: 110 - 100 = 10; or_close = 108; range = 9.3% > 1%
    wide_orb = _orb(h=110.0, l=100.0, close_=108.0)
    gen.on_candle(wide_orb)
    assert gen._states["RELIANCE"].range_rejected

    # Even a breakout after this should be rejected
    breakout = CandleData(open=109, high=115, low=108, close=112,
                          time=_utc(4, 15), volume=5000)
    breakout.__dict__["symbol"] = "RELIANCE"
    result = gen.on_candle(breakout)
    assert result is None


# ── Long signal ───────────────────────────────────────────────────────────────

def test_generates_buy_signal_on_orh_close_break(gen):
    gen.on_candle(_orb(h=105.0, l=104.0, close_=104.5))

    # Close above ORH=105 → BUY
    c = CandleData(open=104.5, high=107, low=104.2, close=105.5,
                   time=_utc(4, 15), volume=5000)
    c.__dict__["symbol"] = "RELIANCE"
    signal = gen.on_candle(c)

    assert signal is not None
    assert signal.signal_type == "BUY"
    assert signal.entry_price == 105.0   # ORH
    assert signal.stop_loss == 104.0     # ORL


def test_buy_signal_has_correct_strategy_metadata(gen):
    gen.on_candle(_orb(h=105.0, l=104.0, close_=104.5))
    c = CandleData(open=104.5, high=107, low=104.2, close=105.5,
                   time=_utc(4, 15), volume=5000)
    c.__dict__["symbol"] = "RELIANCE"
    signal = gen.on_candle(c)

    assert signal.strategy_id == "opening_range_historical_validation"
    assert signal.win_rate == 0.75
    assert signal.trading_date == TRADING_DATE
    assert signal.candidate_date == CANDIDATE_DATE


# ── Short signal ──────────────────────────────────────────────────────────────

def test_generates_sell_signal_on_orl_close_break(gen):
    gen.on_candle(_orb(h=105.0, l=104.0, close_=104.5))

    # Close below ORL=104 → SELL
    c = CandleData(open=104.2, high=104.4, low=103.5, close=103.8,
                   time=_utc(4, 15), volume=5000)
    c.__dict__["symbol"] = "RELIANCE"
    signal = gen.on_candle(c)

    assert signal is not None
    assert signal.signal_type == "SELL"
    assert signal.entry_price == 104.0   # ORL
    assert signal.stop_loss == 105.0     # ORH


# ── One trade per day ─────────────────────────────────────────────────────────

def test_only_one_signal_per_symbol_per_day(gen):
    gen.on_candle(_orb(h=105.0, l=104.0, close_=104.5))

    c1 = CandleData(open=104.5, high=107, low=104.2, close=105.5,
                    time=_utc(4, 15), volume=5000)
    c1.__dict__["symbol"] = "RELIANCE"
    signal1 = gen.on_candle(c1)
    assert signal1 is not None

    # Second breakout (opposite direction) — should be ignored
    c2 = CandleData(open=105, high=106, low=103, close=103.5,
                    time=_utc(4, 30), volume=5000)
    c2.__dict__["symbol"] = "RELIANCE"
    signal2 = gen.on_candle(c2)
    assert signal2 is None


# ── Time filter ───────────────────────────────────────────────────────────────

def test_time_filter_blocks_late_entry(gen):
    """Candles after 12:00 IST (06:30 UTC) should not generate signals."""
    gen.on_candle(_orb(h=105.0, l=104.0, close_=104.5))

    # Candle at 13:00 IST = 07:30 UTC > 06:30 UTC
    late = CandleData(open=104.5, high=107, low=104.2, close=105.5,
                      time=_utc(7, 30), volume=5000)
    late.__dict__["symbol"] = "RELIANCE"
    result = gen.on_candle(late)
    assert result is None


def test_entry_exactly_at_cutoff_generates_signal(gen):
    """12:00 IST = 06:30 UTC should still qualify (boundary inclusive)."""
    gen.on_candle(_orb(h=105.0, l=104.0, close_=104.5))

    at_cutoff = CandleData(open=104.5, high=107, low=104.2, close=105.5,
                           time=_utc(6, 30), volume=5000)
    at_cutoff.__dict__["symbol"] = "RELIANCE"
    result = gen.on_candle(at_cutoff)
    assert result is not None


# ── Lock symbol ───────────────────────────────────────────────────────────────

def test_lock_symbol_prevents_future_signals(gen):
    gen.on_candle(_orb(h=105.0, l=104.0, close_=104.5))
    gen.lock_symbol("RELIANCE")

    c = CandleData(open=104.5, high=107, low=104.2, close=105.5,
                   time=_utc(4, 15), volume=5000)
    c.__dict__["symbol"] = "RELIANCE"
    result = gen.on_candle(c)
    assert result is None


# ── Stats ─────────────────────────────────────────────────────────────────────

def test_stats_incremented_correctly(gen):
    gen.on_candle(_orb(h=105.0, l=104.0, close_=104.5))
    assert gen.stats["first_candle_captured"] == 1
    assert gen.stats["signals_emitted"] == 0

    c = CandleData(open=104.5, high=107, low=104.2, close=105.5,
                   time=_utc(4, 15), volume=5000)
    c.__dict__["symbol"] = "RELIANCE"
    gen.on_candle(c)
    assert gen.stats["signals_emitted"] == 1
    assert gen.stats["trade_locked"] == 1
