"""
Tests for ORHVHistoricalValidator (Phase 2 — Historical Validation).

All tests use pure in-memory data — zero DB or I/O dependencies.
"""

import pytest
from datetime import date, datetime, timezone

from app.models.historical_candle import CandleData
from app.strategy.strategies.opening_range_historical_validation.config import ORHVConfig
from app.strategy.strategies.opening_range_historical_validation.historical_validator import (
    ORHVHistoricalValidator,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utc(year, month, day, hour=4, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _candle(h, l, c, hour, minute=0, year=2024, month=1, day=15) -> CandleData:
    return CandleData(open=c * 0.99, high=h, low=l, close=c, volume=5000,
                      time=_utc(year, month, day, hour, minute))


def _build_day_candles(
    orh: float,
    orl: float,
    or_close: float,
    breakout_side: str,        # "LONG", "SHORT", "NONE"
    eod_close: float,
    year=2024, month=1, day=16,  # Day D+1
) -> list[CandleData]:
    """Build a minimal set of candles for a Phase 3 simulation day."""
    # First candle = ORB
    orb = _candle(h=orh, l=orl, c=or_close, hour=3, minute=45,
                  year=year, month=month, day=day)
    candles = [orb]

    if breakout_side == "LONG":
        # Close above ORH in next candle
        candles.append(_candle(h=orh + 2, l=orl + 0.5, c=orh + 1.5, hour=4,
                                year=year, month=month, day=day))
        # EOD exit
        candles.append(_candle(h=eod_close + 1, l=eod_close - 1, c=eod_close, hour=9, minute=45,
                                year=year, month=month, day=day))
    elif breakout_side == "SHORT":
        # Close below ORL
        candles.append(_candle(h=orl - 0.5, l=orl - 2, c=orl - 1.5, hour=4,
                                year=year, month=month, day=day))
        # EOD exit
        candles.append(_candle(h=eod_close + 1, l=eod_close - 1, c=eod_close, hour=9, minute=45,
                                year=year, month=month, day=day))
    else:
        # No breakout — price stays inside ORB range throughout
        candles.append(_candle(h=orh - 0.1, l=orl + 0.1, c=or_close, hour=4,
                                year=year, month=month, day=day))
        candles.append(_candle(h=orh - 0.1, l=orl + 0.1, c=or_close, hour=9, minute=45,
                                year=year, month=month, day=day))

    return candles


@pytest.fixture
def default_config():
    return ORHVConfig(
        lookback_occurrences=30,
        min_occurrences_required=5,
        qualification_min_wins=21,
        qualification_min_win_rate=0.70,
        max_orb_range_pct=1.0,
        slippage_pct=0.0,   # zero slippage for cleaner P&L checks
        brokerage_per_side=0.0,
        capital_per_trade=100_000.0,
    )


@pytest.fixture
def validator(default_config):
    return ORHVHistoricalValidator(default_config)


# ── No / insufficient history ─────────────────────────────────────────────────

def test_no_prior_occurrences_not_tradable(validator):
    outcome = validator.validate(
        symbol="TEST",
        candidate_date=date(2024, 3, 15),
        prior_setup_dates=[],
        candle_history={},
    )
    assert not outcome.tradable
    assert outcome.occurrences_used == 0


def test_fewer_than_min_occurrences_not_tradable(validator):
    # Only 3 prior setup dates (< min_occurrences_required = 5)
    prior = ["2024-01-10", "2024-01-15", "2024-01-20"]
    history = {}  # no D+1 data → occurrences_with_data = 0
    outcome = validator.validate(
        symbol="TEST",
        candidate_date=date(2024, 3, 15),
        prior_setup_dates=prior,
        candle_history=history,
    )
    assert not outcome.tradable


def test_exactly_min_occurrences_with_data_and_100pct_win_rate(default_config):
    """5 occurrences all winning → win_rate=100% ≥ 70% → tradable."""
    cfg = ORHVConfig(
        min_occurrences_required=5,
        qualification_min_wins=21,
        qualification_min_win_rate=0.70,
        max_orb_range_pct=1.0,
        slippage_pct=0.0,
        brokerage_per_side=0.0,
        capital_per_trade=100_000.0,
    )
    validator = ORHVHistoricalValidator(cfg)

    # 5 setup dates; each D+1 has a LONG breakout producing a profit
    prior = [f"2024-01-{d:02d}" for d in range(10, 15)]  # 5 dates
    candle_history = {}
    for setup_str in prior:
        # D+1 next date
        setup_dt = date.fromisoformat(setup_str)
        next_dt = setup_dt.replace(day=setup_dt.day + 1)
        next_str = next_dt.isoformat()
        # Tight ORB: orh=105, orl=104, or_close=104.5 → range_pct=0.957% < 1%
        candle_history[next_str] = _build_day_candles(
            orh=105, orl=104, or_close=104.5,
            breakout_side="LONG",
            eod_close=109,  # close above entry (orh+slippage) → profit
            year=next_dt.year, month=next_dt.month, day=next_dt.day,
        )

    outcome = validator.validate(
        symbol="TEST",
        candidate_date=date(2024, 2, 1),
        prior_setup_dates=prior,
        candle_history=candle_history,
    )
    # All 5 should win (100% win rate ≥ 70%)
    assert outcome.tradable, f"Expected tradable; rejection: {outcome.rejection_reason}"
    assert outcome.wins == 5
    assert outcome.win_rate == 1.0


# ── Look-ahead bias guard ─────────────────────────────────────────────────────

def test_dates_on_or_after_candidate_are_excluded(validator):
    """Dates >= candidate_date must be filtered out."""
    candidate = date(2024, 3, 15)
    prior = [
        "2024-03-10",   # valid — before candidate
        "2024-03-15",   # SAME as candidate — must be excluded
        "2024-03-16",   # AFTER candidate — must be excluded
    ]
    # Provide D+1 data only for 2024-03-10
    candle_history = {
        "2024-03-11": _build_day_candles(110, 100, 108, "LONG", 115,
                                          year=2024, month=3, day=11),
    }
    outcome = validator.validate(
        symbol="TEST",
        candidate_date=candidate,
        prior_setup_dates=prior,
        candle_history=candle_history,
    )
    # Only "2024-03-10" is safe; that's 1 occurrence < min_occurrences_required
    assert outcome.occurrences_available == 1   # only 1 safe date
    assert not outcome.tradable  # not enough history


# ── Qualification thresholds ──────────────────────────────────────────────────

def _build_history_with_wins(n_total: int, n_wins: int):
    """Build prior_setup_dates and candle_history with n_wins profitable trades."""
    import datetime as dt
    prior = []
    candle_history = {}
    base = dt.date(2023, 1, 1)

    for i in range(n_total):
        setup_date = base + dt.timedelta(days=i * 5)
        setup_str = setup_date.isoformat()
        prior.append(setup_str)

        next_date = setup_date + dt.timedelta(days=1)
        next_str = next_date.isoformat()

        # First n_wins are wins (LONG, EOD above entry); rest are losses (SL hit)
        if i < n_wins:
            # Tight ORB: range=(1/104.5)*100 ≈ 0.957% < 1% — passes range filter
            candle_history[next_str] = _build_day_candles(
                orh=105, orl=104, or_close=104.5,
                breakout_side="LONG", eod_close=109,
                year=next_date.year, month=next_date.month, day=next_date.day,
            )
        else:
            # Tight ORB that SL-hits: LONG entry at ORH=105, SL at ORL=104
            orb = _candle(105, 104, 104.5, hour=3, minute=45,
                          year=next_date.year, month=next_date.month, day=next_date.day)
            entry = _candle(106, 103.5, 105.5, 4,
                            year=next_date.year, month=next_date.month, day=next_date.day)
            sl_hit = _candle(104.1, 103.5, 103.8, 5,  # low=103.5 < ORL=104 → SL
                             year=next_date.year, month=next_date.month, day=next_date.day)
            candle_history[next_str] = [orb, entry, sl_hit]

    return prior, candle_history


def test_21_wins_of_30_is_tradable():
    cfg = ORHVConfig(
        min_occurrences_required=5,
        qualification_min_wins=21,
        qualification_min_win_rate=0.70,
        max_orb_range_pct=1.0,
        slippage_pct=0.0,
        brokerage_per_side=0.0,
        capital_per_trade=100_000.0,
    )
    prior, ch = _build_history_with_wins(30, 21)
    outcome = ORHVHistoricalValidator(cfg).validate(
        symbol="TEST",
        candidate_date=date(2026, 1, 1),
        prior_setup_dates=prior,
        candle_history=ch,
    )
    assert outcome.wins >= 21
    assert outcome.tradable


def test_20_wins_of_30_is_not_tradable():
    """20/30 = 66.7% < 70% and < 21 wins → not tradable."""
    cfg = ORHVConfig(
        min_occurrences_required=5,
        qualification_min_wins=21,
        qualification_min_win_rate=0.70,
        max_orb_range_pct=1.0,
        slippage_pct=0.0,
        brokerage_per_side=0.0,
        capital_per_trade=100_000.0,
    )
    prior, ch = _build_history_with_wins(30, 20)
    outcome = ORHVHistoricalValidator(cfg).validate(
        symbol="TEST",
        candidate_date=date(2026, 1, 1),
        prior_setup_dates=prior,
        candle_history=ch,
    )
    assert not outcome.tradable
    assert outcome.wins == 20


def test_range_filter_blocks_simulation():
    """A D+1 candle with ORB range > 1% should result in a RANGE_FILTER (no win, not tradable)."""
    cfg = ORHVConfig(
        min_occurrences_required=1,
        qualification_min_wins=1,
        qualification_min_win_rate=0.70,
        max_orb_range_pct=1.0,
        slippage_pct=0.0,
        brokerage_per_side=0.0,
        capital_per_trade=100_000.0,
    )
    prior = ["2024-01-10"]
    # D+1 has ORB range > 1%: orh=120, orl=100, or_close=110 → range=(20/110)*100 ≈ 18%
    ch = {"2024-01-11": _build_day_candles(120, 100, 110, "LONG", 130,
                                           year=2024, month=1, day=11)}
    outcome = ORHVHistoricalValidator(cfg).validate(
        symbol="TEST",
        candidate_date=date(2024, 2, 1),
        prior_setup_dates=prior,
        candle_history=ch,
    )
    # Range filter counts as an attempted occurrence with no win
    assert outcome.occurrences_used == 1
    assert outcome.wins == 0
    assert outcome.win_rate == 0.0
    assert not outcome.tradable
    assert outcome.trade_outcomes[0].exit_reason == "RANGE_FILTER"


def test_only_last_30_are_used():
    """Validator should cap at lookback_occurrences=5 even if 50 prior setups exist."""
    cfg = ORHVConfig(
        lookback_occurrences=5,
        min_occurrences_required=5,
        qualification_min_wins=5,
        qualification_min_win_rate=1.0,
        max_orb_range_pct=1.0,
        slippage_pct=0.0,
        brokerage_per_side=0.0,
        capital_per_trade=100_000.0,
    )
    prior, ch = _build_history_with_wins(50, 50)  # 50 prior, all wins
    outcome = ORHVHistoricalValidator(cfg).validate(
        symbol="TEST",
        candidate_date=date(2026, 6, 1),
        prior_setup_dates=prior,
        candle_history=ch,
    )
    assert outcome.occurrences_available == 50
    assert outcome.occurrences_used <= 5
