"""
Monte Carlo Risk Analysis API routes.

POST /api/v1/risk/monte-carlo/run          — Trigger a new simulation run
GET  /api/v1/risk/monte-carlo/results/{run_id} — Retrieve results for a run
GET  /api/v1/risk/monte-carlo/reports/{run_id} — Get all four report types

Routes call MonteCarloService only — no direct repository or Beanie access.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.monte_carlo_run import MonteCarloRunStatus
from app.schemas.monte_carlo import (
    MonteCarloRunRequest,
    MonteCarloRunDetailResponse,
    MonteCarloResultResponse,
    MonteCarloReportResponse,
)
from app.services.monte_carlo_service import (
    MonteCarloService,
    MonteCarloConfigException,
    MonteCarloNotFoundException,
    MonteCarloException,
)
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

_svc = MonteCarloService()


# ── POST /run ─────────────────────────────────────────────────────────────────

@router.post(
    "/run",
    response_model=MonteCarloRunDetailResponse,
    summary="Run Monte Carlo risk analysis",
    description=(
        "Execute a Monte Carlo simulation over historical backtest trade P&Ls. "
        "Runs N simulations per strategy (individual + combined portfolio), then "
        "aggregates drawdown, probability-of-ruin, capital-requirement, and "
        "losing-streak statistics. Results are persisted and retrievable via "
        "/results/{run_id} and /reports/{run_id}. "
        "Synchronous — may take 5–60s depending on simulation_count and trade volume."
    ),
)
async def run_monte_carlo(request: MonteCarloRunRequest) -> MonteCarloRunDetailResponse:
    try:
        run = await _svc.run_simulation(
            strategy_ids=request.strategy_ids,
            simulation_count=request.simulation_count,
            starting_capital=request.starting_capital,
            sampling_method=request.sampling_method,
            ruin_thresholds=request.ruin_thresholds,
            confidence_levels=request.confidence_levels,
            backtest_run_ids=request.backtest_run_ids,
            seed=request.seed,
        )
        results_dict = await _svc.get_results(run.run_id)
        return _build_detail_response(run, results_dict)

    except MonteCarloConfigException as exc:
        raise HTTPException(status_code=422, detail=exc.message)
    except MonteCarloException as exc:
        logger.error("Monte Carlo run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=exc.message)
    except Exception as exc:
        logger.error("Unexpected error in Monte Carlo run: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /results/{run_id} ─────────────────────────────────────────────────────

@router.get(
    "/results/{run_id}",
    response_model=MonteCarloRunDetailResponse,
    summary="Get Monte Carlo results",
    description=(
        "Retrieve the run metadata and all per-strategy result documents "
        "for a completed Monte Carlo run."
    ),
)
async def get_results(run_id: str) -> MonteCarloRunDetailResponse:
    try:
        run = await _svc.get_run(run_id)
        results_dict = await _svc.get_results(run_id)
        return _build_detail_response(run, results_dict)
    except MonteCarloNotFoundException:
        raise HTTPException(status_code=404, detail=f"Monte Carlo run not found: {run_id}")
    except Exception as exc:
        logger.error("Error fetching Monte Carlo results for %s: %s", run_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /reports/{run_id} ─────────────────────────────────────────────────────

@router.get(
    "/reports/{run_id}",
    response_model=MonteCarloReportResponse,
    summary="Get Monte Carlo reports",
    description=(
        "Generate all four report types from stored simulation results: "
        "risk_report, drawdown_report, capital_requirement_report, "
        "and strategy_comparison_report. Reports are generated on-the-fly from "
        "persisted result documents — no re-simulation required."
    ),
)
async def get_reports(run_id: str) -> MonteCarloReportResponse:
    try:
        reports = await _svc.get_reports(run_id)
        return MonteCarloReportResponse(
            run_id=run_id,
            risk_reports=reports["risk_reports"],
            drawdown_reports=reports["drawdown_reports"],
            capital_reports=reports["capital_reports"],
            comparison_report=reports["comparison_report"],
            generated_at=datetime.now(timezone.utc),
        )
    except MonteCarloNotFoundException:
        raise HTTPException(status_code=404, detail=f"Monte Carlo run not found: {run_id}")
    except Exception as exc:
        logger.error("Error generating Monte Carlo reports for %s: %s", run_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Converter helpers ──────────────────────────────────────────────────────────

def _build_detail_response(run, results_dict: dict) -> MonteCarloRunDetailResponse:
    results_data = results_dict.get("results", [])
    result_responses = [
        MonteCarloResultResponse(
            result_id=r["result_id"],
            run_id=r["run_id"],
            strategy_id=r["strategy_id"],
            avg_return=r["avg_return"],
            median_return=r["median_return"],
            best_return=r["best_return"],
            worst_return=r["worst_return"],
            std_return=r["std_return"],
            avg_drawdown=r["avg_drawdown"],
            max_drawdown=r["max_drawdown"],
            probability_of_ruin=r["probability_of_ruin"],
            avg_consecutive_losses=r["avg_consecutive_losses"],
            max_consecutive_losses=r["max_consecutive_losses"],
            return_percentiles=r["return_percentiles"],
            drawdown_percentiles=r["drawdown_percentiles"],
            streak_confidence_intervals=r["streak_confidence_intervals"],
            capital_requirements=r["capital_requirements"],
            trade_count=r["trade_count"],
            simulation_count=r["simulation_count"],
            starting_capital=r["starting_capital"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )
        for r in results_data
    ]

    return MonteCarloRunDetailResponse(
        run_id=run.run_id,
        strategy_ids=run.strategy_ids,
        simulation_count=run.simulation_count,
        status=run.status.value,
        started_at=run.started_at,
        completed_at=run.completed_at,
        configuration=run.configuration,
        error_message=run.error_message,
        created_at=run.created_at,
        metadata=run.metadata,
        results=result_responses,
    )
