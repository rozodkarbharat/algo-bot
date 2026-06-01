"""
Walk-Forward Validation API routes.

POST /api/v1/walk-forward/run          — Trigger a new walk-forward validation run
GET  /api/v1/walk-forward/runs         — List walk-forward runs (paginated)
GET  /api/v1/walk-forward/results/{run_id} — Get full results for a completed run

Routes call WalkForwardService only — no direct repository or Beanie access.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.walk_forward_run import WalkForwardRunStatus
from app.research.walk_forward.window_generator import WalkForwardConfig
from app.schemas.common import PaginatedResponse
from app.schemas.walk_forward import (
    WalkForwardRunRequest,
    WalkForwardRunResponse,
    WalkForwardRunDetailResponse,
    WalkForwardResultsResponse,
    WalkForwardSegmentResponse,
)
from app.services.walk_forward_service import (
    WalkForwardService,
    WalkForwardConfigException,
    WalkForwardNotFoundException,
)
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

_svc = WalkForwardService()


# ── POST /run ─────────────────────────────────────────────────────────────────

@router.post(
    "/run",
    response_model=WalkForwardRunDetailResponse,
    summary="Run walk-forward validation",
    description=(
        "Execute a full walk-forward validation run: generates train/test windows, "
        "optimises strategy parameters on each training window, evaluates on the "
        "corresponding out-of-sample testing window, then aggregates robustness metrics. "
        "Synchronous — may take several minutes. Results are persisted and retrievable "
        "via /results/{run_id}."
    ),
)
async def run_walk_forward(request: WalkForwardRunRequest) -> WalkForwardRunDetailResponse:
    try:
        config = WalkForwardConfig(
            from_date=request.from_date,
            to_date=request.to_date,
            symbols=request.symbols,
            training_months=request.training_months,
            testing_months=request.testing_months,
            step_months=request.step_months,
            strategy_id=request.strategy_id,
            base_probability_threshold=request.base_probability_threshold,
            base_max_orb_range_pct=request.base_max_orb_range_pct,
            base_max_entry_time_ist=request.base_max_entry_time_ist,
            base_sl_buffer_pct=request.base_sl_buffer_pct,
            capital_per_trade=request.capital_per_trade,
            slippage_pct=request.slippage_pct,
            brokerage_per_side=request.brokerage_per_side,
        )
        run = await _svc.run_walk_forward(config)
        return _run_to_detail_response(run)

    except WalkForwardConfigException as exc:
        raise HTTPException(status_code=422, detail=exc.message)
    except Exception as exc:
        logger.error("Walk-forward run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /runs ─────────────────────────────────────────────────────────────────

@router.get(
    "/runs",
    response_model=PaginatedResponse[WalkForwardRunResponse],
    summary="List walk-forward runs",
)
async def list_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Optional[WalkForwardRunStatus] = Query(default=None),
) -> PaginatedResponse[WalkForwardRunResponse]:
    runs, total = await _svc.list_runs(status=status, page=page, page_size=page_size)
    items = [_run_to_response(r) for r in runs]
    return PaginatedResponse.build(items=items, total=total, page=page, page_size=page_size)


# ── GET /results/{run_id} ─────────────────────────────────────────────────────

@router.get(
    "/results/{run_id}",
    response_model=WalkForwardResultsResponse,
    summary="Get walk-forward results for a completed run",
)
async def get_results(run_id: str) -> WalkForwardResultsResponse:
    try:
        results_dict = await _svc.get_results(run_id)
        return _results_to_response(results_dict)
    except WalkForwardNotFoundException:
        raise HTTPException(status_code=404, detail=f"Walk-forward run not found: {run_id}")


# ── Converters ────────────────────────────────────────────────────────────────

def _run_to_response(run) -> WalkForwardRunResponse:
    return WalkForwardRunResponse(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        strategy_name=run.strategy_name,
        status=run.status.value,
        started_at=run.started_at,
        completed_at=run.completed_at,
        configuration=run.configuration,
        error_message=run.error_message,
        created_at=run.created_at,
    )


def _run_to_detail_response(run) -> WalkForwardRunDetailResponse:
    return WalkForwardRunDetailResponse(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        strategy_name=run.strategy_name,
        status=run.status.value,
        started_at=run.started_at,
        completed_at=run.completed_at,
        configuration=run.configuration,
        error_message=run.error_message,
        created_at=run.created_at,
        metadata=run.metadata,
    )


def _results_to_response(results_dict: dict) -> WalkForwardResultsResponse:
    run_data = results_dict.get("run_data", {})
    segments_data = results_dict.get("segments_data", [])

    run_detail = WalkForwardRunDetailResponse(
        run_id=run_data.get("run_id", ""),
        strategy_id=run_data.get("strategy_id", ""),
        strategy_name=run_data.get("strategy_name", ""),
        status=run_data.get("status", ""),
        started_at=run_data.get("started_at"),
        completed_at=run_data.get("completed_at"),
        configuration=run_data.get("configuration", {}),
        error_message=run_data.get("error_message"),
        created_at=run_data.get("created_at"),
        metadata={},
    )

    segments = [
        WalkForwardSegmentResponse(
            segment_id=seg.get("segment_id", ""),
            run_id=run_data.get("run_id", ""),
            segment_number=seg.get("segment_number", 0),
            training_start=seg.get("training_start"),
            training_end=seg.get("training_end"),
            testing_start=seg.get("testing_start"),
            testing_end=seg.get("testing_end"),
            selected_parameters=seg.get("selected_parameters", {}),
            optimization_score=seg.get("optimization_score", 0.0),
            metrics=seg.get("metrics", {}),
            status=seg.get("status", ""),
            error_message=seg.get("error_message"),
            created_at=seg.get("created_at"),
        )
        for seg in segments_data
    ]

    return WalkForwardResultsResponse(
        run=run_detail,
        segments=segments,
        aggregated=results_dict.get("aggregated", {}),
        robustness=results_dict.get("robustness", {}),
        segment_count=len(segments),
        completed_count=sum(1 for s in segments if s.status == "completed"),
    )
