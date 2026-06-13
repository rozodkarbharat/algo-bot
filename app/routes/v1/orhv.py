"""
ORHV API routes.

GET  /api/v1/orhv/today                   — ORHV shortlist for next execution day (UI)
POST /api/v1/orhv/run                     — Manual full pipeline (sync + detect + validate)
GET  /api/v1/orhv/status                  — ORHV run-manager state

GET  /api/v1/orhv/candidates              — Phase 1 candidate setups for a date
GET  /api/v1/orhv/tradable                — Phase 2 tradable rows for execution date
GET  /api/v1/orhv/validations             — Phase 2 validation records
GET  /api/v1/orhv/signals                 — Phase 3 live signals
GET  /api/v1/orhv/statistics              — Per-symbol rolling statistics

POST /api/v1/orhv/detect                  — Trigger Phase 1 detection for a date
POST /api/v1/orhv/validate                — Trigger Phase 2 validation for a date
POST /api/v1/orhv/cycle                   — Run Phase 1 + 2 for a date (no Angel One sync)

Literal paths (/today, /run, /status) MUST be declared before dynamic segments.
"""

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

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
    ORHVShortlistEntryResponse,
    ORHVShortlistResponse,
    ORHVShortlistRunRequest,
    ORHVShortlistRunResponse,
    ORHVShortlistStatusResponse,
    ORHVSignalResponse,
    ORHVStatisticsResponse,
    ORHVSymbolRunRequest,
    ORHVSymbolRunResponse,
    ORHVValidationResponse,
    ORHVValidationSummaryResponse,
)
from app.services.orhv_service import ORHVService, ORHVShortlistResult, orhv_run_manager
from app.strategy.strategies.opening_range_historical_validation.constants import (
    STRATEGY_ID,
    STRATEGY_NAME,
)
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight

router = APIRouter()
logger = get_logger(__name__)

_svc = ORHVService()
_setup_repo = ORHVSetupRepository()
_val_repo = ORHVValidationRepository()
_signal_repo = ORHVSignalRepository()
_stats_repo = ORHVStatisticsRepository()


# ── Shortlist (UI) — declare before /candidates, /tradable, etc. ─────────────


def _build_orhv_shortlist_response(result: ORHVShortlistResult) -> ORHVShortlistResponse:
    entries = [
        ORHVShortlistEntryResponse(
            symbol=e.symbol,
            candidate_date=e.candidate_date,
            execution_date=e.execution_date,
            orh_d=e.orh_d,
            orl_d=e.orl_d,
            orb_range_pct=e.orb_range_pct,
            win_rate=e.win_rate,
            win_rate_pct=round(e.win_rate * 100, 2),
            wins=e.wins,
            losses=e.losses,
            occurrences_used=e.occurrences_used,
            occurrences_available=e.occurrences_available,
            is_candidate=e.is_candidate,
            tradable=e.tradable,
            reason_skipped=e.reason_skipped,
        )
        for e in result.entries
    ]
    tradable = sum(1 for e in entries if e.tradable)
    return ORHVShortlistResponse(
        strategy_id=STRATEGY_ID,
        strategy_name=STRATEGY_NAME,
        trading_date=result.execution_date,
        candidate_date=result.candidate_date,
        total_candidates=result.total_candidates_checked,
        total_phase1_scanned=result.total_phase1_scanned,
        total_tradable=tradable,
        threshold_win_rate_pct=round(result.threshold_used * 100, 1),
        generated_at=datetime.now(timezone.utc),
        entries=entries,
    )


@router.get(
    "/today",
    response_model=ORHVShortlistResponse,
    summary="ORHV shortlist for the next execution session",
)
async def get_orhv_today_shortlist(
    win_rate_threshold: Optional[float] = Query(
        None,
        ge=0.0,
        le=1.0,
        description="Optional win-rate filter override (0.0–1.0)",
    ),
) -> ORHVShortlistResponse:
    """Read-only shortlist from MongoDB (Phase 1 candidates + Phase 2 validations)."""
    result = await _svc.generate_shortlist(win_rate_threshold=win_rate_threshold)
    return _build_orhv_shortlist_response(result)


@router.post(
    "/run",
    response_model=ORHVShortlistRunResponse,
    status_code=202,
    summary="Manually trigger ORHV shortlist pipeline",
)
async def run_orhv_shortlist(
    body: ORHVShortlistRunRequest = Body(default_factory=ORHVShortlistRunRequest),
) -> ORHVShortlistRunResponse:
    """
    Full pipeline (default): sync Day D candles → Phase 1 → Phase 2 → shortlist.

    Returns immediately with ``status=accepted``; poll ``GET /orhv/status`` until
    ``running`` is false, then refresh ``GET /orhv/today``.
    """
    logger.info(
        "ORHV manual run: target_date=%s full_pipeline=%s",
        body.target_date, body.full_pipeline,
    )
    await orhv_run_manager.start_background(
        target_date=body.target_date,
        win_rate_threshold=body.win_rate_threshold,
        trigger="manual",
        full_pipeline=body.full_pipeline,
    )
    from app.utils.trading_day import get_next_trading_day, last_completed_trading_day

    exec_date = body.target_date or get_next_trading_day(last_completed_trading_day())
    return ORHVShortlistRunResponse(
        status="accepted",
        target_date=exec_date,
        total_checked=0,
        total_shortlisted=0,
        duration_seconds=0.0,
        full_pipeline=body.full_pipeline,
    )


@router.get(
    "/status",
    response_model=ORHVShortlistStatusResponse,
    summary="ORHV run-manager status",
)
async def get_orhv_shortlist_status() -> ORHVShortlistStatusResponse:
    snap = orhv_run_manager.snapshot()
    return ORHVShortlistStatusResponse(**snap.to_dict())


@router.post(
    "/run-symbol",
    response_model=ORHVSymbolRunResponse,
    summary="Test ORHV for a single symbol (full pipeline or Phase 2 only)",
)
async def run_orhv_symbol(body: ORHVSymbolRunRequest) -> ORHVSymbolRunResponse:
    """
    Run the ORHV strategy for ONE symbol synchronously.

      * mode='full'   — sync + detect history, then detect + validate the setup
                        day. Use to bootstrap a stock with no stored data.
      * mode='phase2' — validate against already-stored history to check the
                        symbol's prior performance (no broker calls).
    """
    mode = (body.mode or "full").lower()
    if mode not in ("full", "phase2"):
        raise HTTPException(status_code=422, detail="mode must be 'full' or 'phase2'.")
    if not body.symbol or not body.symbol.strip():
        raise HTTPException(status_code=422, detail="symbol is required.")

    try:
        result = await _svc.run_symbol_test(
            symbol=body.symbol.strip(),
            mode=mode,
            target_date=body.target_date,
        )
    except Exception as exc:
        logger.error("ORHV symbol test failed for %s: %s", body.symbol, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    return ORHVSymbolRunResponse(
        symbol=result.symbol,
        mode=result.mode,
        candidate_date=result.candidate_date,
        execution_date=result.execution_date,
        has_phase1_setup=result.has_phase1_setup,
        is_candidate=result.is_candidate,
        phase1_reason=result.phase1_reason,
        validated=result.validated,
        occurrences_available=result.occurrences_available,
        occurrences_used=result.occurrences_used,
        wins=result.wins,
        losses=result.losses,
        win_rate=result.win_rate,
        win_rate_pct=round(result.win_rate * 100, 2),
        tradable=result.tradable,
        reason=result.reason,
        orh_d=result.orh_d,
        orl_d=result.orl_d,
        candles_synced=result.candles_synced,
        history_candle_days=result.history_candle_days,
        history_detection_days=result.history_detection_days,
        duration_seconds=result.duration_seconds,
        message=result.message,
    )


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


# ── Shortlist for a specific date (UI history) ───────────────────────────────
# MUST be the LAST GET route: the dynamic /{target_date} segment would
# otherwise shadow literal paths like /candidates, /tradable, /signals.

@router.get(
    "/{target_date}",
    response_model=ORHVShortlistResponse,
    summary="ORHV shortlist for a specific execution date",
)
async def get_orhv_shortlist_for_date(
    target_date: date,
    win_rate_threshold: Optional[float] = Query(
        None,
        ge=0.0,
        le=1.0,
        description="Optional win-rate filter override (0.0–1.0)",
    ),
) -> ORHVShortlistResponse:
    """
    Read-only ORHV shortlist for any historical execution date.

    Regenerates from stored MongoDB data (Phase 1 candidates + Phase 2
    validations) for the requested execution date; returns empty if the
    underlying data for that date is not present.
    """
    result = await _svc.generate_shortlist(
        target_date=target_date,
        win_rate_threshold=win_rate_threshold,
    )
    return _build_orhv_shortlist_response(result)
