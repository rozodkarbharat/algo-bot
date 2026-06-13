"""Unit tests for shortlist API response mapping."""

from datetime import date

from app.routes.v1.shortlist import _build_entry_response, _build_response, _ui_direction
from app.services.shortlist_service import ShortlistEntry, ShortlistResult


def test_ui_direction_maps_up_down() -> None:
    assert _ui_direction("UP") == "BULLISH"
    assert _ui_direction("DOWN") == "BEARISH"
    assert _ui_direction("bullish") == "BULLISH"


def test_build_entry_response_includes_dashboard_fields() -> None:
    entry = ShortlistEntry(
        symbol="RELIANCE",
        direction="UP",
        first_candle_high=2500.0,
        first_candle_low=2480.0,
        breakout_price=2500.0,
        move_percent=1.2,
        continuation_probability=0.72,
        total_occurrences=40,
        yesterday_date=date(2026, 5, 30),
    )
    resp = _build_entry_response(entry)
    assert resp.direction == "BULLISH"
    assert resp.orb_high == 2500.0
    assert resp.orb_low == 2480.0
    assert resp.entry_trigger == 2500.0
    assert resp.stop_loss == 2480.0
    assert resp.probability == 0.72
    assert resp.tradable is True
    assert resp.reason_skipped is None
    assert resp.first_candle_range_pct > 0


def test_build_entry_response_passes_through_skipped_reason() -> None:
    entry = ShortlistEntry(
        symbol="HDFC",
        direction="UP",
        first_candle_high=1700.0,
        first_candle_low=1680.0,
        breakout_price=None,
        move_percent=None,
        continuation_probability=0.4,
        total_occurrences=12,
        yesterday_date=date(2026, 5, 30),
        tradable=False,
        reason_skipped="Probability 40.0% below threshold 60.0%",
    )
    resp = _build_entry_response(entry)
    assert resp.tradable is False
    assert resp.reason_skipped == "Probability 40.0% below threshold 60.0%"


def test_build_response_counts_pool_vs_tradable() -> None:
    """total_tradable counts only entries with tradable=True; entries may also
    contain skipped rows that are surfaced for UI visibility."""
    tradable_entry = ShortlistEntry(
        symbol="TCS",
        direction="DOWN",
        first_candle_high=4000.0,
        first_candle_low=3950.0,
        breakout_price=3950.0,
        move_percent=None,
        continuation_probability=0.65,
        total_occurrences=30,
        yesterday_date=date(2026, 6, 1),
        tradable=True,
        reason_skipped=None,
    )
    skipped_entry = ShortlistEntry(
        symbol="INFY",
        direction="UP",
        first_candle_high=1500.0,
        first_candle_low=1480.0,
        breakout_price=None,
        move_percent=None,
        continuation_probability=0.45,
        total_occurrences=20,
        yesterday_date=date(2026, 6, 1),
        tradable=False,
        reason_skipped="Probability 45.0% below threshold 60.0%",
    )
    result = ShortlistResult(
        target_date=date(2026, 6, 2),
        yesterday=date(2026, 6, 1),
        entries=[tradable_entry, skipped_entry],
        total_candidates_checked=5,
        threshold_used=0.6,
    )
    resp = _build_response(result)
    assert resp.trading_date == date(2026, 6, 2)
    assert resp.total_candidates == 5         # one-side day pool
    assert resp.total_tradable == 1           # only the TCS entry passed
    assert len(resp.entries) == 2             # both surfaced to the UI
    assert resp.entries[0].direction == "BEARISH"
    assert resp.entries[0].tradable is True
    assert resp.entries[1].tradable is False
    assert resp.entries[1].reason_skipped is not None
