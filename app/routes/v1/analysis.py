"""
One-side day analysis API routes.

GET  /api/v1/one-side-days              — Paginated list of OSD records (filterable)
GET  /api/v1/one-side-days/{symbol}     — OSD history for a specific symbol
GET  /api/v1/continuation-stats         — All continuation probability statistics
GET  /api/v1/continuation-stats/{symbol} — Stats for a specific symbol
POST /api/v1/analysis/run-detection     — Trigger historical OSD detection
POST /api/v1/analysis/run-probability   — Trigger probability recalculation

Routes call services only — no direct repository or Beanie access here.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.exceptions import DocumentNotFoundException
from app.repositories.continuation_statistic_repository import ContinuationStatisticRepository
from app.repositories.one_side_day_repository import OneSideDayRepository
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.strategy import (
    ContinuationStatResponse,
    DetectionSummaryResponse,
    OneSideDayResponse,
    ProbabilitySummaryResponse,
    RecalculateProbabilityRequest,
    RunDetectionForDateRequest,
    RunDetectionRequest,
)
from app.services.strategy_service import StrategyService
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight

router = APIRouter()
logger = get_logger(__name__)

_strategy_svc = StrategyService()
_osd_repo = OneSideDayRepository()
_cont_repo = ContinuationStatisticRepository()


# ── One-Side Day records ──────────────────────────────────────────────────────

@router.get(
    "/one-side-days",
    response_model=PaginatedResponse[OneSideDayResponse],
    summary="List one-side day records",
)
async def list_one_side_days(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    one_side_only: bool = Query(default=False, description="Return only valid one-side days"),
    from_date: Optional[date] = Query(None, description="Filter from date (inclusive)"),
    to_date: Optional[date] = Query(None, description="Filter to date (inclusive)"),
) -> PaginatedResponse[OneSideDayResponse]:
    """
    Return paginated one-side day analysis records.

    Supports filtering by symbol, date range, and one_side_only flag.
    """
    skip = (page - 1) * page_size

    if symbol:
        symbol = symbol.upper()
        if from_date and to_date:
            from_dt = date_to_utc_midnight(from_date)
            to_dt = date_to_utc_midnight(to_date)
            if one_side_only:
                records = await _osd_repo.get_one_side_only(symbol, from_dt, to_dt)
            else:
                records = await _osd_repo.get_between_dates(symbol, from_dt, to_dt)
        else:
            records = await _osd_repo.get_by_symbol(symbol, limit=page_size + skip, skip=0)
    else:
        records = await _osd_repo.get_all(limit=page_size + skip, skip=0)

    total = len(records)
    paged = records[skip : skip + page_size]
    items = [OneSideDayResponse.from_document(r) for r in paged]
    return PaginatedResponse.build(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/one-side-days/{symbol}",
    response_model=PaginatedResponse[OneSideDayResponse],
    summary="Get one-side day history for a symbol",
)
async def get_one_side_days_for_symbol(
    symbol: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    one_side_only: bool = Query(default=False),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
) -> PaginatedResponse[OneSideDayResponse]:
    """Return the one-side day history for a specific symbol."""
    symbol = symbol.upper()
    skip = (page - 1) * page_size

    if from_date and to_date:
        from_dt = date_to_utc_midnight(from_date)
        to_dt = date_to_utc_midnight(to_date)
        if one_side_only:
            records = await _osd_repo.get_one_side_only(symbol, from_dt, to_dt)
        else:
            records = await _osd_repo.get_between_dates(symbol, from_dt, to_dt)
    else:
        records = await _osd_repo.get_by_symbol(symbol, limit=10000, skip=0)

    total = len(records)
    paged = records[skip : skip + page_size]
    items = [OneSideDayResponse.from_document(r) for r in paged]
    return PaginatedResponse.build(items=items, total=total, page=page, page_size=page_size)


# ── Continuation statistics ───────────────────────────────────────────────────

@router.get(
    "/continuation-stats",
    response_model=PaginatedResponse[ContinuationStatResponse],
    summary="List continuation probability statistics",
)
async def list_continuation_stats(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    tradable_only: bool = Query(default=False, description="Only return tradable stocks"),
) -> PaginatedResponse[ContinuationStatResponse]:
    """
    Return continuation probability statistics for all tracked symbols.

    Results are ordered by probability descending (highest edge first).
    """
    skip = (page - 1) * page_size
    if tradable_only:
        all_stats = await _cont_repo.get_tradable_stocks()
    else:
        all_stats = await _cont_repo.get_all_statistics(limit=500, skip=0)

    total = len(all_stats)
    paged = all_stats[skip : skip + page_size]
    items = [ContinuationStatResponse.from_document(s) for s in paged]
    return PaginatedResponse.build(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/continuation-stats/{symbol}",
    response_model=ContinuationStatResponse,
    summary="Get continuation stats for a symbol",
)
async def get_continuation_stats_for_symbol(symbol: str) -> ContinuationStatResponse:
    """Return continuation probability statistics for a specific symbol."""
    stat = await _cont_repo.get_by_symbol(symbol.upper())
    if stat is None:
        raise DocumentNotFoundException("ContinuationStatistic", symbol.upper())
    return ContinuationStatResponse.from_document(stat)


# ── Trigger actions ───────────────────────────────────────────────────────────

@router.post(
    "/analysis/run-detection",
    response_model=DetectionSummaryResponse,
    summary="Run historical one-side day detection",
)
async def run_detection(body: RunDetectionRequest) -> DetectionSummaryResponse:
    """
    Trigger OSD detection for a historical date range.

    This is a synchronous operation — it returns when detection is complete.
    For large ranges (> 1 year), consider scheduling via the APScheduler jobs instead.
    """
    logger.info(
        "Manual OSD detection triggered: %s → %s, symbols=%s",
        body.from_date, body.to_date, body.symbols,
    )
    result = await _strategy_svc.run_detection_range(
        from_date=body.from_date,
        to_date=body.to_date,
        symbols=body.symbols,
    )
    return DetectionSummaryResponse(**result.to_dict())


@router.post(
    "/analysis/run-detection-date",
    response_model=DetectionSummaryResponse,
    summary="Run one-side day detection for a single date",
)
async def run_detection_for_date(body: RunDetectionForDateRequest) -> DetectionSummaryResponse:
    """Trigger OSD detection for a single trading date."""
    logger.info(
        "Manual single-date OSD detection triggered: %s, symbols=%s",
        body.trading_date, body.symbols,
    )
    result = await _strategy_svc.run_detection_for_date(
        trading_date=body.trading_date,
        symbols=body.symbols,
    )
    return DetectionSummaryResponse(**result.to_dict())


@router.post(
    "/analysis/run-probability",
    response_model=ProbabilitySummaryResponse,
    summary="Recalculate continuation probability statistics",
)
async def run_probability_calculation(
    body: RecalculateProbabilityRequest,
) -> ProbabilitySummaryResponse:
    """
    Trigger a full recalculation of continuation probability for all (or specified) symbols.

    Designed to be called after the EOD detection job has run for the day.
    """
    logger.info(
        "Manual probability calculation triggered: symbols=%s, lookback=%s",
        body.symbols, body.lookback_days,
    )
    result = await _strategy_svc.calculate_all_continuation_stats(
        symbols=body.symbols,
        lookback_days=body.lookback_days,
    )
    return ProbabilitySummaryResponse(**result.to_dict())
