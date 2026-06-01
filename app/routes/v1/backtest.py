"""
Backtest API routes.

POST /api/v1/backtest/run                  — Trigger a new backtest run (synchronous)
GET  /api/v1/backtest/runs                 — List backtest runs (paginated)
GET  /api/v1/backtest/runs/{run_id}        — Get a single run by ID
GET  /api/v1/backtest/trades/{run_id}      — List trades for a run (paginated)
GET  /api/v1/backtest/metrics/{run_id}     — Get aggregate metrics for a run
GET  /api/v1/backtest/analytics/{run_id}   — Get deep analytics for a run

Routes call services only — no direct repository or Beanie access.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.exceptions import BacktestConfigException, BacktestNotFoundException
from app.models.backtest_run import BacktestRunStatus
from app.models.backtest_trade import ExitReason
from app.schemas.backtest import (
    BacktestAnalyticsResponse,
    BacktestMetricsResponse,
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestTradeResponse,
    EntryTimeSlotResponse,
    SymbolPerformanceResponse,
)
from app.schemas.common import MessageResponse, PaginatedResponse
from app.services.backtest_analytics_service import BacktestAnalyticsService
from app.services.backtest_service import BacktestService
from app.strategy.backtest_engine import BacktestConfig
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

_backtest_svc   = BacktestService()
_analytics_svc  = BacktestAnalyticsService()


# ── POST /run ─────────────────────────────────────────────────────────────────

@router.post(
    "/run",
    response_model=BacktestRunResponse,
    summary="Run a backtest",
    description=(
        "Execute a full historical backtest for the One-Side ORB strategy. "
        "This call is synchronous and may take several minutes for large date ranges. "
        "The run is persisted to MongoDB and can be queried via /runs/{run_id} afterwards."
    ),
)
async def run_backtest(request: BacktestRunRequest) -> BacktestRunResponse:
    try:
        config = BacktestConfig(
            from_date=request.from_date,
            to_date=request.to_date,
            symbols=request.symbols,
            probability_threshold=request.probability_threshold,
            max_orb_range_pct=request.max_orb_range_pct,
            max_entry_time_ist=request.max_entry_time_ist,
            capital_per_trade=request.capital_per_trade,
            slippage_pct=request.slippage_pct,
            brokerage_per_side=request.brokerage_per_side,
            sl_buffer_pct=request.sl_buffer_pct,
        )
        run = await _backtest_svc.run_backtest(
            config, strategy_id=request.strategy_id
        )
        return _run_to_response(run)

    except BacktestConfigException as exc:
        raise HTTPException(status_code=422, detail=exc.message)
    except Exception as exc:
        logger.error("Backtest run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /runs ─────────────────────────────────────────────────────────────────

@router.get(
    "/runs",
    response_model=PaginatedResponse[BacktestRunResponse],
    summary="List backtest runs",
)
async def list_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    strategy_name: Optional[str] = Query(default=None),
    status: Optional[BacktestRunStatus] = Query(default=None),
) -> PaginatedResponse[BacktestRunResponse]:
    runs, total = await _backtest_svc.list_runs(
        strategy_name=strategy_name,
        status=status,
        page=page,
        page_size=page_size,
    )
    items = [_run_to_response(r) for r in runs]
    return PaginatedResponse.build(items=items, total=total, page=page, page_size=page_size)


# ── GET /runs/{run_id} ────────────────────────────────────────────────────────

@router.get(
    "/runs/{run_id}",
    response_model=BacktestRunResponse,
    summary="Get a backtest run by ID",
)
async def get_run(run_id: str) -> BacktestRunResponse:
    try:
        run = await _backtest_svc.get_run(run_id)
        return _run_to_response(run)
    except BacktestNotFoundException:
        raise HTTPException(status_code=404, detail=f"Backtest run not found: {run_id}")


# ── GET /trades/{run_id} ──────────────────────────────────────────────────────

@router.get(
    "/trades/{run_id}",
    response_model=PaginatedResponse[BacktestTradeResponse],
    summary="List simulated trades for a run",
)
async def list_trades(
    run_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    symbol: Optional[str] = Query(default=None, description="Filter by symbol"),
    exit_reason: Optional[ExitReason] = Query(default=None, description="Filter by exit reason"),
) -> PaginatedResponse[BacktestTradeResponse]:
    try:
        trades, total = await _backtest_svc.list_trades(
            run_id=run_id,
            symbol=symbol,
            exit_reason=exit_reason,
            page=page,
            page_size=page_size,
        )
        items = [_trade_to_response(t) for t in trades]
        return PaginatedResponse.build(items=items, total=total, page=page, page_size=page_size)
    except BacktestNotFoundException:
        raise HTTPException(status_code=404, detail=f"Backtest run not found: {run_id}")


# ── GET /metrics/{run_id} ─────────────────────────────────────────────────────

@router.get(
    "/metrics/{run_id}",
    response_model=BacktestMetricsResponse,
    summary="Get aggregate performance metrics for a run",
)
async def get_metrics(run_id: str) -> BacktestMetricsResponse:
    try:
        metrics = await _backtest_svc.get_metrics(run_id)
        if metrics is None:
            raise HTTPException(
                status_code=404,
                detail=f"Metrics not yet available for run {run_id}. "
                       "The run may still be in progress.",
            )
        return BacktestMetricsResponse(
            run_id=metrics.run_id,
            total_trades=metrics.total_trades,
            winning_trades=metrics.winning_trades,
            losing_trades=metrics.losing_trades,
            no_entry_days=metrics.no_entry_days,
            total_candidate_days=metrics.total_candidate_days,
            win_rate=metrics.win_rate,
            sl_hit_rate=metrics.sl_hit_rate,
            breakout_success_rate=metrics.breakout_success_rate,
            total_pnl=metrics.total_pnl,
            avg_pnl_per_trade=metrics.avg_pnl_per_trade,
            avg_win=metrics.avg_win,
            avg_loss=metrics.avg_loss,
            max_win=metrics.max_win,
            max_loss=metrics.max_loss,
            max_drawdown=metrics.max_drawdown,
            max_drawdown_percent=metrics.max_drawdown_percent,
            profit_factor=metrics.profit_factor,
            expectancy=metrics.expectancy,
            sharpe_ratio=metrics.sharpe_ratio,
            avg_risk_reward=metrics.avg_risk_reward,
            max_consecutive_wins=metrics.max_consecutive_wins,
            max_consecutive_losses=metrics.max_consecutive_losses,
            per_symbol_metrics=metrics.per_symbol_metrics,
            daily_pnl=metrics.daily_pnl,
            monthly_pnl=metrics.monthly_pnl,
            created_at=metrics.created_at,
        )
    except BacktestNotFoundException:
        raise HTTPException(status_code=404, detail=f"Backtest run not found: {run_id}")


# ── GET /analytics/{run_id} ───────────────────────────────────────────────────

@router.get(
    "/analytics/{run_id}",
    response_model=BacktestAnalyticsResponse,
    summary="Get deep analytics for a completed backtest run",
)
async def get_analytics(run_id: str) -> BacktestAnalyticsResponse:
    try:
        result = await _analytics_svc.generate_analytics(run_id)
        return BacktestAnalyticsResponse(
            run_id=result.run_id,
            best_symbols=[
                SymbolPerformanceResponse(**vars(s)) for s in result.best_symbols
            ],
            worst_symbols=[
                SymbolPerformanceResponse(**vars(s)) for s in result.worst_symbols
            ],
            entry_time_analysis=[
                EntryTimeSlotResponse(**vars(e)) for e in result.entry_time_analysis
            ],
            long_metrics=result.long_metrics,
            short_metrics=result.short_metrics,
            monthly_pnl_heatmap=result.monthly_pnl_heatmap,
            orb_range_buckets=result.orb_range_buckets,
            probability_sensitivity=result.probability_sensitivity,
            metadata=result.metadata,
        )
    except BacktestNotFoundException:
        raise HTTPException(status_code=404, detail=f"Backtest run not found: {run_id}")
    except Exception as exc:
        logger.error("Analytics generation failed for run %s: %s", run_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Converters ────────────────────────────────────────────────────────────────

def _run_to_response(run) -> BacktestRunResponse:
    return BacktestRunResponse(
        run_id=run.run_id,
        strategy_id=getattr(run, "strategy_id", "one_side_orb"),
        strategy_name=run.strategy_name,
        strategy_version=getattr(run, "strategy_version", "1.0.0"),
        status=run.status.value,
        symbols=run.symbols,
        backtest_from=run.backtest_from.date() if run.backtest_from else None,
        backtest_to=run.backtest_to.date() if run.backtest_to else None,
        started_at=run.started_at,
        completed_at=run.completed_at,
        configuration=run.configuration,
        summary_metrics=run.summary_metrics,
        error_message=run.error_message,
        created_at=run.created_at,
    )


def _trade_to_response(trade) -> BacktestTradeResponse:
    return BacktestTradeResponse(
        run_id=trade.run_id,
        symbol=trade.symbol,
        trading_date=trade.trading_date.date(),
        trade_side=trade.trade_side.value,
        breakout_side=trade.breakout_side,
        orb_high=trade.orb_high,
        orb_low=trade.orb_low,
        probability_score=trade.probability_score,
        entry_time=trade.entry_time,
        entry_price=trade.entry_price,
        stop_loss=trade.stop_loss,
        exit_time=trade.exit_time,
        exit_price=trade.exit_price,
        exit_reason=trade.exit_reason.value,
        quantity=trade.quantity,
        capital_used=trade.capital_used,
        pnl=trade.pnl,
        pnl_percent=trade.pnl_percent,
        risk_reward=trade.risk_reward,
        metadata=trade.metadata,
    )
