"""
Research and optimization API routes.

POST /api/v1/research/run                        — Trigger a new research run
GET  /api/v1/research/runs                       — List research runs (paginated)
GET  /api/v1/research/runs/{run_id}              — Get a single run
GET  /api/v1/research/optimization-results       — Optimization results for a run
GET  /api/v1/research/stock-analytics            — Stock leaderboard
GET  /api/v1/research/time-analytics/{run_id}    — Time analytics (from report)
GET  /api/v1/research/failure-analysis/{run_id}  — Failure diagnostics (from report)
GET  /api/v1/research/reports/{run_id}           — Full research report

Routes call ResearchService only — no direct repository or Beanie access.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.exceptions import ResearchConfigException, ResearchNotFoundException
from app.models.research_run import ResearchRunStatus
from app.research.parameter_optimizer import ParameterGrid, ResearchConfig
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.research import (
    OptimizationResultResponse,
    ResearchReportResponse,
    ResearchRunDetailResponse,
    ResearchRunRequest,
    ResearchRunResponse,
    StockAnalyticsResponse,
)
from app.services.research_service import ResearchService
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

_research_svc = ResearchService()


# ── POST /run ─────────────────────────────────────────────────────────────────

@router.post(
    "/run",
    response_model=ResearchRunDetailResponse,
    summary="Run parameter optimization + analytics",
    description=(
        "Execute a full research run: univariate parameter sweep + stock/time/"
        "market condition/failure analytics. Synchronous — may take several minutes. "
        "Results are persisted and retrievable via /runs/{run_id} and /reports/{run_id}."
    ),
)
async def run_research(request: ResearchRunRequest) -> ResearchRunDetailResponse:
    try:
        grid = ParameterGrid(
            probability_thresholds=(
                request.probability_thresholds
                or [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]
            ),
            orb_range_filters=(
                request.orb_range_filters
                or [0.50, 0.70, 1.00, 1.20, 1.50]
            ),
            entry_cutoff_times=(
                request.entry_cutoff_times
                or ["10:00", "10:30", "11:00", "11:30"]
            ),
            sl_buffers=(
                request.sl_buffers
                or [0.00, 0.05, 0.10, 0.15]
            ),
        )
        config = ResearchConfig(
            from_date=request.from_date,
            to_date=request.to_date,
            symbols=request.symbols,
            base_probability_threshold=request.base_probability_threshold,
            base_max_orb_range_pct=request.base_max_orb_range_pct,
            base_max_entry_time_ist=request.base_max_entry_time_ist,
            base_sl_buffer_pct=request.base_sl_buffer_pct,
            capital_per_trade=request.capital_per_trade,
            slippage_pct=request.slippage_pct,
            brokerage_per_side=request.brokerage_per_side,
            grid=grid,
        )
        run = await _research_svc.run_research(config)
        return _run_to_detail_response(run)

    except ResearchConfigException as exc:
        raise HTTPException(status_code=422, detail=exc.message)
    except Exception as exc:
        logger.error("Research run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /runs ─────────────────────────────────────────────────────────────────

@router.get(
    "/runs",
    response_model=PaginatedResponse[ResearchRunResponse],
    summary="List research runs",
)
async def list_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Optional[ResearchRunStatus] = Query(default=None),
) -> PaginatedResponse[ResearchRunResponse]:
    runs, total = await _research_svc.list_runs(
        status=status, page=page, page_size=page_size
    )
    items = [_run_to_response(r) for r in runs]
    return PaginatedResponse.build(items=items, total=total, page=page, page_size=page_size)


# ── GET /runs/{run_id} ────────────────────────────────────────────────────────

@router.get(
    "/runs/{run_id}",
    response_model=ResearchRunDetailResponse,
    summary="Get a research run by ID",
)
async def get_run(run_id: str) -> ResearchRunDetailResponse:
    try:
        run = await _research_svc.get_run(run_id)
        return _run_to_detail_response(run)
    except ResearchNotFoundException:
        raise HTTPException(status_code=404, detail=f"Research run not found: {run_id}")


# ── GET /optimization-results ─────────────────────────────────────────────────

@router.get(
    "/optimization-results",
    response_model=list[OptimizationResultResponse],
    summary="Get parameter optimization results for a run",
)
async def get_optimization_results(
    run_id: str = Query(..., description="Research run ID"),
    parameter_name: Optional[str] = Query(
        default=None,
        description="Filter to a specific parameter (e.g. 'probability_threshold')",
    ),
) -> list[OptimizationResultResponse]:
    try:
        results = await _research_svc.get_optimization_results(
            run_id=run_id, parameter_name=parameter_name
        )
        return [_opt_to_response(r) for r in results]
    except ResearchNotFoundException:
        raise HTTPException(status_code=404, detail=f"Research run not found: {run_id}")


# ── GET /stock-analytics ──────────────────────────────────────────────────────

@router.get(
    "/stock-analytics",
    response_model=list[StockAnalyticsResponse],
    summary="Get stock performance analytics leaderboard",
)
async def get_stock_analytics(
    metric: str = Query(
        default="expectancy",
        description="Sort metric: expectancy | total_pnl | win_rate | profit_factor",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    min_trades: int = Query(default=3, ge=1, description="Minimum trades to include"),
) -> list[StockAnalyticsResponse]:
    records = await _research_svc.get_stock_analytics(
        metric=metric, limit=limit, min_trades=min_trades
    )
    return [_spa_to_response(r) for r in records]


# ── GET /time-analytics/{run_id} ──────────────────────────────────────────────

@router.get(
    "/time-analytics/{run_id}",
    summary="Get time-of-day analytics for a research run",
)
async def get_time_analytics(run_id: str) -> dict:
    try:
        report = await _research_svc.get_report(run_id)
        return report.get("time_edge", {})
    except ResearchNotFoundException:
        raise HTTPException(status_code=404, detail=f"Research run not found: {run_id}")


# ── GET /failure-analysis/{run_id} ────────────────────────────────────────────

@router.get(
    "/failure-analysis/{run_id}",
    summary="Get SL and failure diagnostics for a research run",
)
async def get_failure_analysis(run_id: str) -> dict:
    try:
        report = await _research_svc.get_report(run_id)
        return report.get("failure_diagnostics", {})
    except ResearchNotFoundException:
        raise HTTPException(status_code=404, detail=f"Research run not found: {run_id}")


# ── GET /reports/{run_id} ─────────────────────────────────────────────────────

@router.get(
    "/reports/{run_id}",
    response_model=ResearchReportResponse,
    summary="Get the full research report for a completed run",
)
async def get_report(run_id: str) -> ResearchReportResponse:
    try:
        report = await _research_svc.get_report(run_id)
        if not report:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Report not yet available for run {run_id}. "
                    "The run may still be in progress or failed."
                ),
            )
        return ResearchReportResponse(
            run_id=report.get("run_id", run_id),
            executive_summary=report.get("executive_summary", {}),
            parameter_sensitivity=report.get("parameter_sensitivity", {}),
            stock_rankings=report.get("stock_rankings", {}),
            time_edge=report.get("time_edge", {}),
            market_conditions=report.get("market_conditions", {}),
            failure_diagnostics=report.get("failure_diagnostics", {}),
            recommendations=report.get("recommendations", []),
            metadata=report.get("metadata", {}),
        )
    except ResearchNotFoundException:
        raise HTTPException(status_code=404, detail=f"Research run not found: {run_id}")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Report retrieval failed for %s: %s", run_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Converters ────────────────────────────────────────────────────────────────

def _run_to_response(run) -> ResearchRunResponse:
    return ResearchRunResponse(
        run_id=run.run_id,
        status=run.status.value,
        started_at=run.started_at,
        completed_at=run.completed_at,
        configuration=run.configuration,
        error_message=run.error_message,
        created_at=run.created_at,
    )


def _run_to_detail_response(run) -> ResearchRunDetailResponse:
    return ResearchRunDetailResponse(
        run_id=run.run_id,
        status=run.status.value,
        started_at=run.started_at,
        completed_at=run.completed_at,
        configuration=run.configuration,
        error_message=run.error_message,
        created_at=run.created_at,
        metadata=run.metadata,
    )


def _opt_to_response(r) -> OptimizationResultResponse:
    return OptimizationResultResponse(
        run_id=r.run_id,
        parameter_name=r.parameter_name,
        parameter_value=r.parameter_value,
        total_trades=r.total_trades,
        winning_trades=r.winning_trades,
        losing_trades=r.losing_trades,
        win_rate=r.win_rate,
        sl_hit_rate=r.sl_hit_rate,
        breakout_success_rate=r.breakout_success_rate,
        total_pnl=r.total_pnl,
        avg_pnl_per_trade=r.avg_pnl_per_trade,
        expectancy=r.expectancy,
        profit_factor=r.profit_factor,
        max_drawdown=r.max_drawdown,
        sharpe_ratio=r.sharpe_ratio,
        created_at=r.created_at,
    )


def _spa_to_response(r) -> StockAnalyticsResponse:
    return StockAnalyticsResponse(
        symbol=r.symbol,
        total_trades=r.total_trades,
        winning_trades=r.winning_trades,
        losing_trades=r.losing_trades,
        win_rate=r.win_rate,
        sl_hit_rate=r.sl_hit_rate,
        breakout_success_rate=r.breakout_success_rate,
        total_pnl=r.total_pnl,
        avg_pnl=r.avg_pnl,
        max_win=r.max_win,
        max_loss=r.max_loss,
        expectancy=r.expectancy,
        profit_factor=r.profit_factor,
        max_drawdown=r.max_drawdown,
        avg_orb_range_pct=r.avg_orb_range_pct,
        avg_move_after_breakout_pct=r.avg_move_after_breakout_pct,
        best_breakout_time_range=r.best_breakout_time_range,
        last_run_id=r.last_run_id,
        updated_at=r.updated_at,
    )
