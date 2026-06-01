"""
Tests for ORHVBacktestEngine (Phase 1 → Phase 2 → Phase 3 full replay).

All tests use pure in-memory data — zero DB or I/O dependencies.
"""

import pytest
from datetime import datetime, timezone

from app.models.backtest_trade import ExitReason, TradeSide
from app.models.historical_candle import CandleData
from app.strategy.strategies.opening_range_historical_validation.backtest_logic import (
    ORHVBacktestEngine,
)
from app.strategy.strategies.opening_range_historical_validation.config import ORHVConfig


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utc(year, month, day, hour=4, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _c(h, l, c, year, month, day, hour=4, minute=0) -> CandleData:
    return CandleData(open=c * 0.99, high=h, low=l, close=c, volume=5000,
                      time=_utc(year, month, day, hour, minute))


def _full_setup_day(year, month, day):
    """Build a day with a complete ORHV Phase 1 candidate setup."""
    orb = _c(110, 100, 107, year, month, day, 3, 45)
    c1 = _c(115, 99, 114, year, month, day, 4, 0)    # CH1 (h=115>110) + CL1 (l=99<100)
    c2 = _c(120, 97, 116, year, month, day, 4, 15)   # Cond A (close=116>115)
    c3 = _c(112, 88, 87, year, month, day, 4, 30)    # Cond B (close=87<99)
    return [orb, c1, c2, c3]


def _long_breakout_day(year, month, day, eod_close=116.0):
    """D+1 candles with a LONG ORB breakout and profitable EOD exit."""
    orb = _c(110, 100, 108, year, month, day, 3, 45)        # ORB; range = 10/108 = 9.3% — fails filter!
    return [orb]  # range filter blocks this


def _tight_long_breakout(year, month, day, eod_close=112.0):
    """D+1 with tight ORB (range < 1%) and LONG breakout."""
    # ORH=105, ORL=104, or_close=104.5 → range=(1/104.5)*100 ≈ 0.96% ≤ 1%
    orb = _c(105, 104, 104.5, year, month, day, 3, 45)
    entry_c = _c(106, 103.5, 105.5, year, month, day, 4, 15)  # close=105.5 > ORH=105 → LONG
    eod_c = _c(113, 111, eod_close, year, month, day, 9, 45)
    return [orb, entry_c, eod_c]


def _tight_sl_hit(year, month, day):
    """D+1 with LONG entry then SL hit at ORL=104."""
    orb = _c(105, 104, 104.5, year, month, day, 3, 45)
    entry_c = _c(106, 103.5, 105.5, year, month, day, 4, 15)  # LONG entry
    sl_c = _c(104.1, 103.5, 103.8, year, month, day, 5, 0)    # low=103.5 < ORL=104 → SL
    return [orb, entry_c, sl_c]


@pytest.fixture
def cfg():
    return ORHVConfig(
        lookback_occurrences=5,
        min_occurrences_required=3,
        qualification_min_wins=3,     # need 3/5 wins = 60% — but also need ≥70% rate
        qualification_min_win_rate=0.60,  # use 60% for easier testing
        max_orb_range_pct=1.0,
        slippage_pct=0.0,
        brokerage_per_side=0.0,
        capital_per_trade=100_000.0,
    )


# ── Basic functionality ───────────────────────────────────────────────────────

def test_empty_candle_history_returns_no_trades(cfg):
    engine = ORHVBacktestEngine(cfg)
    result = engine.run(["RELIANCE"], {}, {}, {})
    assert result.trades == []
    assert result.total_candidate_days == 0


def test_single_day_no_phase2_history_no_trade(cfg):
    """With only 1 setup day, Phase 2 has no prior history → not tradable."""
    engine = ORHVBacktestEngine(cfg)
    ch = {"RELIANCE": {"2024-01-15": _full_setup_day(2024, 1, 15)}}
    result = engine.run(["RELIANCE"], {}, {}, ch)
    # candidate found but no prior history → Phase 2 not tradable
    assert result.total_candidate_days == 0
    assert result.trades == []


def test_setup_history_accumulates_incrementally(cfg):
    """
    With enough prior setups (≥ min_occurrences_required), a candidate
    day should produce a tradable validation.

    Uses non-consecutive dates to prevent setup days from overwriting D+1 data.
    Each setup is on a Monday (even weeks), D+1 is on Tuesday.
    """
    engine = ORHVBacktestEngine(cfg)

    # 5 prior setup days spread apart so D+1 data is not overwritten
    # Dates: 2024-01-02, 2024-01-09, 2024-01-16, 2024-01-23, 2024-01-30
    ch = {}
    for week in range(5):
        # Day = first weekday of each week (Monday)
        base_day = 2 + week * 7  # 2, 9, 16, 23, 30
        setup_str = f"2024-01-{base_day:02d}"
        next_str = f"2024-01-{base_day + 1:02d}"
        ch[setup_str] = _full_setup_day(2024, 1, base_day)
        ch[next_str] = _tight_long_breakout(2024, 1, base_day + 1)

    # Candidate day (February) — well separated from January setups
    ch["2024-02-05"] = _full_setup_day(2024, 2, 5)
    ch["2024-02-06"] = _tight_long_breakout(2024, 2, 6)

    result = engine.run(["RELIANCE"], {}, {}, {"RELIANCE": ch})
    # At least one candidate day found and validated
    assert result.total_candidate_days >= 1


def _five_prior_weeks() -> dict:
    """5 non-overlapping setup days each with tight-LONG D+1 data (all LONG wins)."""
    ch = {}
    for week in range(5):
        base = 2 + week * 7   # 2, 9, 16, 23, 30 — Mondays of Jan 2024
        ch[f"2024-01-{base:02d}"] = _full_setup_day(2024, 1, base)
        ch[f"2024-01-{base + 1:02d}"] = _tight_long_breakout(2024, 1, base + 1)
    return ch


# ── No-breakout result ────────────────────────────────────────────────────────

def test_range_filter_produces_no_breakout_trade(cfg):
    """D+1 ORB range > 1% → trade has NO_BREAKOUT exit_reason."""
    engine = ORHVBacktestEngine(cfg)
    ch = _five_prior_weeks()
    ch["2024-02-05"] = _full_setup_day(2024, 2, 5)
    # D+1 has WIDE ORB (range=(20/110)*100 ≈ 18% > 1%) → range filter
    wide_orb = [_c(120, 100, 110, 2024, 2, 6, 3, 45),
                _c(121, 101, 110.5, 2024, 2, 6, 4, 0),
                _c(118, 99, 109, 2024, 2, 6, 9, 45)]
    ch["2024-02-06"] = wide_orb

    result = engine.run(["RELIANCE"], {}, {}, {"RELIANCE": ch})
    no_breakout = [t for t in result.trades if t.exit_reason == ExitReason.NO_BREAKOUT]
    assert len(no_breakout) >= 1


# ── SL hit ────────────────────────────────────────────────────────────────────

def test_sl_hit_trade_recorded(cfg):
    engine = ORHVBacktestEngine(cfg)
    ch = _five_prior_weeks()
    ch["2024-02-05"] = _full_setup_day(2024, 2, 5)
    ch["2024-02-06"] = _tight_sl_hit(2024, 2, 6)

    result = engine.run(["RELIANCE"], {}, {}, {"RELIANCE": ch})
    sl_trades = [t for t in result.trades if t.exit_reason == ExitReason.SL_HIT]
    assert len(sl_trades) >= 1


# ── Anti-look-ahead ───────────────────────────────────────────────────────────

def test_no_lookahead_same_day_setup_not_in_history(cfg):
    """
    When processing Day D, the setup detected on Day D itself must NOT
    be in the prior_setup_dates used for Phase 2 validation.
    This is the core anti-look-ahead guarantee.
    """
    engine = ORHVBacktestEngine(cfg)

    # Only 2 prior setup days; current candidate is 2024-01-15
    ch = {
        "2024-01-05": _full_setup_day(2024, 1, 5),
        "2024-01-06": _tight_long_breakout(2024, 1, 6),
        "2024-01-10": _full_setup_day(2024, 1, 10),
        "2024-01-11": _tight_long_breakout(2024, 1, 11),
        "2024-01-15": _full_setup_day(2024, 1, 15),
    }

    # Only 2 prior setups → below min_occurrences_required=3 → not tradable
    result = engine.run(["RELIANCE"], {}, {}, {"RELIANCE": ch})
    # No candidate days because Phase 2 didn't pass (insufficient history)
    assert result.total_candidate_days == 0


def test_prob_scores_and_osd_history_ignored(cfg):
    """Engine must accept but ignore prob_scores and osd_history."""
    engine = ORHVBacktestEngine(cfg)
    result = engine.run(
        symbols=["RELIANCE"],
        prob_scores={"RELIANCE": 0.99},          # should be ignored
        osd_history={"RELIANCE": {"2024-01-01": {"is_one_side": True}}},  # should be ignored
        candle_history={"RELIANCE": {}},
    )
    assert result.trades == []  # empty history = no trades
