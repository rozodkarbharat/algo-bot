"""Unit tests for ORHV shortlist API mapping."""

from datetime import date

from app.routes.v1.orhv import _build_orhv_shortlist_response
from app.services.orhv_service import ORHVShortlistEntry, ORHVShortlistResult


def test_build_orhv_shortlist_response_counts_tradable() -> None:
    result = ORHVShortlistResult(
        execution_date=date(2026, 6, 2),
        candidate_date=date(2026, 6, 1),
        entries=[
            ORHVShortlistEntry(
                symbol="RELIANCE",
                candidate_date=date(2026, 6, 1),
                execution_date=date(2026, 6, 2),
                orh_d=2500.0,
                orl_d=2480.0,
                orb_range_pct=0.81,
                win_rate=0.75,
                wins=23,
                losses=7,
                occurrences_used=30,
                occurrences_available=30,
                tradable=True,
            ),
            ORHVShortlistEntry(
                symbol="TCS",
                candidate_date=date(2026, 6, 1),
                execution_date=date(2026, 6, 2),
                orh_d=4000.0,
                orl_d=3950.0,
                orb_range_pct=1.27,
                win_rate=0.55,
                wins=15,
                losses=12,
                occurrences_used=27,
                occurrences_available=27,
                tradable=False,
                reason_skipped="Win rate 55.0% below threshold 70.0%",
            ),
        ],
        total_candidates_checked=2,
        threshold_used=0.7,
    )
    resp = _build_orhv_shortlist_response(result)
    assert resp.trading_date == date(2026, 6, 2)
    assert resp.candidate_date == date(2026, 6, 1)
    assert resp.total_candidates == 2
    assert resp.total_tradable == 1
    assert resp.entries[0].tradable is True
    assert resp.entries[1].reason_skipped is not None
