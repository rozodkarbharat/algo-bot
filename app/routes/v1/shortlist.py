"""
Daily shortlist API routes.

GET /api/v1/shortlist/today          — Today's tradable shortlist
GET /api/v1/shortlist/{date}         — Shortlist for a specific date
GET /api/v1/shortlist/tradable-stocks — All stocks with tradable=True

Routes call services only — no direct repository or Beanie access here.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Query

from app.schemas.strategy import ShortlistEntryResponse, ShortlistResponse
from app.services.shortlist_service import ShortlistService
from app.utils.logger import get_logger
from app.utils.trading_day import last_completed_trading_day

router = APIRouter()
logger = get_logger(__name__)

_shortlist_svc = ShortlistService()


@router.get(
    "/today",
    response_model=ShortlistResponse,
    summary="Today's tradable shortlist",
)
async def get_today_shortlist(
    probability_threshold: Optional[float] = Query(
        None,
        ge=0.0,
        le=1.0,
        description="Override the default probability threshold (0.0–1.0)",
    ),
) -> ShortlistResponse:
    """
    Generate the tradable shortlist for today's trading session.

    Logic:
      - Looks up yesterday's one-side day records
      - Filters by continuation probability >= threshold
      - Returns sorted by probability (highest edge first)

    This call is safe to make multiple times — the result is deterministic
    for a given trading day.
    """
    result = await _shortlist_svc.generate_shortlist(
        probability_threshold=probability_threshold,
    )
    return _build_response(result)


@router.get(
    "/{target_date}",
    response_model=ShortlistResponse,
    summary="Shortlist for a specific date",
)
async def get_shortlist_for_date(
    target_date: date,
    probability_threshold: Optional[float] = Query(
        None, ge=0.0, le=1.0
    ),
) -> ShortlistResponse:
    """
    Generate the shortlist for any historical or future trading date.

    Useful for backtesting: "what would the shortlist have looked like on date X?"
    Data must be available in MongoDB for the date range to produce results.
    """
    result = await _shortlist_svc.generate_shortlist(
        target_date=target_date,
        probability_threshold=probability_threshold,
    )
    return _build_response(result)


@router.get(
    "/tradable-stocks",
    response_model=list[ShortlistEntryResponse],
    summary="All stocks with tradable continuation probability",
)
async def get_tradable_stocks() -> list[ShortlistEntryResponse]:
    """
    Return all stocks that currently have tradable=True continuation statistics.

    These are stocks that have historically shown >= threshold continuation
    probability with a sufficient sample size.

    Note: This returns the full tradable universe, not filtered by yesterday's
    one-side day condition. Use /shortlist/today for the actionable daily list.
    """
    stats = await _shortlist_svc.get_tradable_stocks()
    return [
        ShortlistEntryResponse(
            symbol=s.symbol,
            direction="UP",  # direction is not pre-determined in the stats
            first_candle_high=0.0,
            first_candle_low=0.0,
            continuation_probability=s.continuation_probability,
            continuation_probability_pct=round(s.continuation_probability * 100, 2),
            total_occurrences=s.total_occurrences,
            yesterday_date=last_completed_trading_day(),
        )
        for s in stats
    ]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_response(result) -> ShortlistResponse:
    """Convert ShortlistResult to ShortlistResponse schema."""
    from app.config.settings import settings

    entries = [
        ShortlistEntryResponse(
            symbol=e.symbol,
            direction=e.direction,
            first_candle_high=e.first_candle_high,
            first_candle_low=e.first_candle_low,
            breakout_price=e.breakout_price,
            move_percent=e.move_percent,
            continuation_probability=e.continuation_probability,
            continuation_probability_pct=round(e.continuation_probability * 100, 2),
            total_occurrences=e.total_occurrences,
            yesterday_date=e.yesterday_date,
        )
        for e in result.entries
    ]
    return ShortlistResponse(
        target_date=result.target_date,
        yesterday=result.yesterday,
        total_candidates=len(entries),
        total_checked=result.total_candidates_checked,
        threshold_pct=round(result.threshold_used * 100, 1),
        entries=entries,
    )
