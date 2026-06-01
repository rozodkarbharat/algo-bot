"""
ORHV API routes.

GET  /api/v1/orhv/candidates              — Today's (or any date's) Phase 1 candidate setups
GET  /api/v1/orhv/tradable                — Symbols tradable tomorrow (Phase 2 validated)
GET  /api/v1/orhv/validations             — Phase 2 validation records
GET  /api/v1/orhv/signals                 — Phase 3 live signals
GET  /api/v1/orhv/statistics              — Per-symbol rolling statistics

POST /api/v1/orhv/detect                  — Trigger Phase 1 detection for a date
POST /api/v1/orhv/validate                — Trigger Phase 2 validation for a date
POST /api/v1/orhv/cycle                   — Run Phase 1 + 2 for a date
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.repositories.orhv_setup_repository import ORHVSetupRepository
from app.repositories.orhv_validation_repository import ORHVValidationRepository
from app.repositories.orhv_signal_repository import ORHVSignalRepository
from app.repositories.orhv_statistics_repository import ORHVStatisticsRepository
from app.schemas.common import MessageResponse
from app.schemas.orhv import (
    ORHVDetectionSummaryResponse,
    ORHVRunCycleRequest,
    ORHVRunDetectionRequest,
    ORHVRunValidationRequest,
    ORHVSetupResponse,
    ORHVSignalResponse,
    ORHVStatisticsResponse,
    ORHVValidationResponse,
    ORHVValidationSummaryResponse,
)
from app.services.orhv_service import ORHVService
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight

router = APIRouter()
logger = get_logger(__name__)

_svc = ORHVService()
_setup_repo = ORHVSetupRepository()
_val_repo = ORHVValidationRepository()
_signal_repo = ORHVSignalRepository()
_stats_repo = ORHVStatisticsRepository()


# ── Candidate setups (Phase 1) ────────────────────────────────────────────────

@router.get(
    "/candidates",
    response_model=list[ORHVSetupResponse],
    summary="List Phase 1 candidate setups for a date",
)
async def get_candidates(
    setup_date: date = Query(default=None, description="Date to query (defaults to today)"),
    symbol: Optional[str] = Query(default=None),
) -> list[ORHVSetupResponse]:
    """Return all Phase 1 candidate detections for a given date."""
    from app.utils.trading_day import last_completed_trading_day
    effective_date = setup_date or last_completed_trading_day()
    dt = date_to_utc_midnight(effective_date)

    if symbol:
        from app.utils.market_time import date_to_utc_midnight as _d
        doc = await _setup_repo.get_by_symbol_and_date(symbol.upper(), dt)
        docs = [doc] if doc else []
    else:
        docs = await _setup_repo.get_candidates_on_date(dt)

    return [ORHVSetupResponse.from_document(d) for d in docs]


# ── Tradable symbols (Phase 2 result) ─────────────────────────────────────────

@router.get(
    "/tradable",
    response_model=list[ORHVValidationResponse],
    summary="List symbols tradable for a given execution date",
)
async def get_tradable(
    execution_date: date = Query(default=None, description="Day D+1 — trading date"),
) -> list[ORHVValidationResponse]:
    """Return validations where tradable=True for the given execution date."""
    from app.utils.trading_day import last_completed_trading_day
    import datetime
    effective_date = execution_date or (last_completed_trading_day() + datetime.timedelta(days=1))
    dt = date_to_utc_midnight(effective_date)
    docs = await _val_repo.get_tradable_for_date(dt)
    return [ORHVValidationResponse.from_document(d) for d in docs]


# ── Validation records (Phase 2) ──────────────────────────────────────────────

@router.get(
    "/validations",
    response_model=list[ORHVValidationResponse],
    summary="List Phase 2 validation records",
)
async def get_validations(
    symbol: str = Query(..., description="NSE ticker symbol"),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[ORHVValidationResponse]:
    docs = await _val_repo.get_recent_for_symbol(symbol.upper(), limit=limit)
    return [ORHVValidationResponse.from_document(d) for d in docs]


# ── Signals (Phase 3) ────────────────────────────────────────────────────────

@router.get(
    "/signals",
    response_model=list[ORHVSignalResponse],
    summary="List Phase 3 live signals",
)
async def get_signals(
    limit: int = Query(default=20, ge=1, le=100),
    trading_date: Optional[date] = Query(default=None),
) -> list[ORHVSignalResponse]:
    if trading_date:
        dt = date_to_utc_midnight(trading_date)
        docs = await _signal_repo.get_for_date(dt)
    else:
        docs = await _signal_repo.get_recent(limit=limit)
    return [ORHVSignalResponse.from_document(d) for d in docs]


# ── Statistics ────────────────────────────────────────────────────────────────

@router.get(
    "/statistics",
    response_model=list[ORHVStatisticsResponse],
    summary="Per-symbol ORHV performance statistics",
)
async def get_statistics(
    symbol: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ORHVStatisticsResponse]:
    docs = await _svc.get_statistics(symbol)
    return [ORHVStatisticsResponse.from_document(d) for d in docs[:limit]]


# ── Trigger endpoints ─────────────────────────────────────────────────────────

@router.post(
    "/detect",
    response_model=ORHVDetectionSummaryResponse,
    summary="Trigger Phase 1 setup detection for a date",
)
async def run_detection(request: ORHVRunDetectionRequest) -> ORHVDetectionSummaryResponse:
    try:
        summary = await _svc.run_detection_for_date(
            trading_date=request.trading_date,
            symbols=request.symbols,
        )
        return ORHVDetectionSummaryResponse(**summary.to_dict())
    except Exception as exc:
        logger.error("ORHV detection failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/validate",
    response_model=ORHVValidationSummaryResponse,
    summary="Trigger Phase 2 historical validation for a date",
)
async def run_validation(request: ORHVRunValidationRequest) -> ORHVValidationSummaryResponse:
    try:
        summary = await _svc.run_validation_for_date(
            candidate_date=request.candidate_date,
            symbols=request.symbols,
        )
        return ORHVValidationSummaryResponse(**summary.to_dict())
    except Exception as exc:
        logger.error("ORHV validation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/cycle",
    summary="Run Phase 1 + Phase 2 for a date",
)
async def run_full_cycle(request: ORHVRunCycleRequest) -> dict:
    try:
        detection, validation = await _svc.run_full_cycle_for_date(
            trading_date=request.trading_date,
            symbols=request.symbols,
        )
        return {
            "detection": detection.to_dict(),
            "validation": validation.to_dict(),
        }
    except Exception as exc:
        logger.error("ORHV full cycle failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
