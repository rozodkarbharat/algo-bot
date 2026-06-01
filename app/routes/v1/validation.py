"""
Validation API routes.

GET /api/v1/validation/signals      — Signal quality analysis
GET /api/v1/validation/slippage     — Slippage analysis
GET /api/v1/validation/latency      — Latency analysis
GET /api/v1/validation/reality-gap  — Backtest vs paper vs live comparison
GET /api/v1/validation/health       — Strategy health score 0-100

Routes call ValidationService only — no direct repository or Beanie access.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.validation_service import ValidationService
from app.schemas.validation import (
    SignalQualityResponse, SlippageResponse, LatencyResponse,
    RealityGapResponse, HealthResponse, ModeMetricsSchema,
    StrategyHealthSchema, HealthDimensionSchema, LatencyPercentilesSchema,
    SymbolSlippageSchema, StrategySignalQualitySchema,
)
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)
_svc = ValidationService()


# ── GET /signals ──────────────────────────────────────────────────────────────

@router.get(
    "/signals",
    response_model=SignalQualityResponse,
    summary="Signal quality analysis",
)
async def get_signal_quality(
    strategy_id: Optional[str] = Query(default=None, description="Filter by strategy ID"),
    from_date: Optional[datetime] = Query(default=None, description="Start date (UTC). Default: 30 days ago"),
    to_date: Optional[datetime] = Query(default=None, description="End date (UTC). Default: now"),
) -> SignalQualityResponse:
    now = datetime.now(timezone.utc)
    if from_date is None:
        from_date = now - timedelta(days=30)
    if to_date is None:
        to_date = now

    try:
        result = await _svc.get_signal_quality(
            from_date=from_date,
            to_date=to_date,
            strategy_id=strategy_id,
        )
        if result is None:
            return SignalQualityResponse(
                generated_count=0,
                executed_count=0,
                missed_count=0,
                conversion_rate=0.0,
                miss_reasons={},
                by_strategy=[],
                sample_days=0,
                from_date=from_date,
                to_date=to_date,
            )
        return _signal_quality_to_response(result, from_date, to_date)
    except Exception as exc:
        logger.error("Signal quality endpoint failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /slippage ──────────────────────────────────────────────────────────────

@router.get(
    "/slippage",
    response_model=SlippageResponse,
    summary="Slippage analysis",
)
async def get_slippage(
    strategy_id: Optional[str] = Query(default=None),
    from_date: Optional[datetime] = Query(default=None),
    to_date: Optional[datetime] = Query(default=None),
    trading_mode: str = Query(default="PAPER", description="PAPER, LIVE, or COMBINED"),
) -> SlippageResponse:
    now = datetime.now(timezone.utc)
    if from_date is None:
        from_date = now - timedelta(days=30)
    if to_date is None:
        to_date = now

    try:
        result = await _svc.get_slippage(
            from_date=from_date,
            to_date=to_date,
            strategy_id=strategy_id,
            trading_mode=trading_mode,
        )
        if result is None:
            return SlippageResponse(
                avg_entry_slippage_bps=0.0,
                avg_exit_slippage_bps=0.0,
                worst_entry_slippage_bps=0.0,
                worst_exit_slippage_bps=0.0,
                total_slippage_cost_inr=0.0,
                symbol_breakdown=[],
                sample_count=0,
                trading_mode=trading_mode.upper(),
                from_date=from_date,
                to_date=to_date,
            )
        return _slippage_to_response(result, from_date, to_date)
    except Exception as exc:
        logger.error("Slippage endpoint failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /latency ──────────────────────────────────────────────────────────────

@router.get(
    "/latency",
    response_model=LatencyResponse,
    summary="Latency analysis",
)
async def get_latency(
    strategy_id: Optional[str] = Query(default=None),
    from_date: Optional[datetime] = Query(default=None),
    to_date: Optional[datetime] = Query(default=None),
) -> LatencyResponse:
    now = datetime.now(timezone.utc)
    if from_date is None:
        from_date = now - timedelta(days=30)
    if to_date is None:
        to_date = now

    try:
        result = await _svc.get_latency(
            from_date=from_date,
            to_date=to_date,
            strategy_id=strategy_id,
        )
        if result is None:
            zero_p = LatencyPercentilesSchema(p50_ms=0.0, p95_ms=0.0, p99_ms=0.0, max_ms=0.0)
            return LatencyResponse(
                avg_signal_latency_ms=0.0,
                signal_latency_percentiles=zero_p,
                avg_execution_latency_ms=0.0,
                execution_latency_percentiles=zero_p,
                avg_ws_latency_ms=None,
                ws_latency_percentiles=None,
                sample_count=0,
                high_latency_signals=[],
                from_date=from_date,
                to_date=to_date,
            )
        return _latency_to_response(result, from_date, to_date)
    except Exception as exc:
        logger.error("Latency endpoint failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /reality-gap ──────────────────────────────────────────────────────────

@router.get(
    "/reality-gap",
    response_model=RealityGapResponse,
    summary="Backtest vs paper vs live comparison",
)
async def get_reality_gap(
    strategy_id: str = Query(default="one_side_orb", description="Strategy ID (required)"),
    from_date: Optional[datetime] = Query(default=None),
    to_date: Optional[datetime] = Query(default=None),
) -> RealityGapResponse:
    now = datetime.now(timezone.utc)
    if from_date is None:
        from_date = now - timedelta(days=30)
    if to_date is None:
        to_date = now

    try:
        result = await _svc.get_reality_gap(
            strategy_id=strategy_id,
            from_date=from_date,
            to_date=to_date,
        )
        if result is None:
            return RealityGapResponse(
                backtest=None,
                paper=None,
                live=None,
                paper_win_rate_gap=None,
                paper_pnl_gap=None,
                paper_drawdown_gap=None,
                paper_expectancy_gap=None,
                live_win_rate_gap=None,
                live_pnl_gap=None,
                live_drawdown_gap=None,
                live_expectancy_gap=None,
                live_vs_paper_win_rate_gap=None,
                live_vs_paper_pnl_gap=None,
                strategy_id=strategy_id,
                analysis_period_days=(to_date - from_date).days,
                from_date=from_date,
                to_date=to_date,
            )
        return _reality_gap_to_response(result, from_date, to_date)
    except Exception as exc:
        logger.error("Reality gap endpoint failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /health ───────────────────────────────────────────────────────────────

@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Strategy health score 0-100",
)
async def get_health(
    strategy_id: Optional[str] = Query(
        default=None,
        description="Strategy ID. If omitted, scores all active strategies",
    ),
    from_date: Optional[datetime] = Query(default=None),
    to_date: Optional[datetime] = Query(default=None),
) -> HealthResponse:
    now = datetime.now(timezone.utc)
    if from_date is None:
        from_date = now - timedelta(days=30)
    if to_date is None:
        to_date = now

    try:
        results = await _svc.get_health(
            from_date=from_date,
            to_date=to_date,
            strategy_id=strategy_id,
        )
        if not results:
            return HealthResponse(strategies=[], from_date=from_date, to_date=to_date)
        strategies = [_health_result_to_schema(r) for r in results]
        return HealthResponse(strategies=strategies, from_date=from_date, to_date=to_date)
    except Exception as exc:
        logger.error("Health endpoint failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Converters ────────────────────────────────────────────────────────────────

def _signal_quality_to_response(result, from_date: datetime, to_date: datetime) -> SignalQualityResponse:
    by_strategy = [
        StrategySignalQualitySchema(
            strategy_id=s.strategy_id,
            generated=s.generated,
            executed=s.executed,
            missed=s.missed,
            conversion_rate=s.conversion_rate,
        )
        for s in result.by_strategy
    ]
    return SignalQualityResponse(
        generated_count=result.generated_count,
        executed_count=result.executed_count,
        missed_count=result.missed_count,
        conversion_rate=result.conversion_rate,
        miss_reasons=result.miss_reasons,
        by_strategy=by_strategy,
        sample_days=result.sample_days,
        from_date=from_date,
        to_date=to_date,
    )


def _slippage_to_response(result, from_date: datetime, to_date: datetime) -> SlippageResponse:
    symbol_breakdown = [
        SymbolSlippageSchema(
            symbol=s.symbol,
            avg_entry_slippage_bps=s.avg_entry_slippage_bps,
            avg_exit_slippage_bps=s.avg_exit_slippage_bps,
            worst_entry_slippage_bps=s.worst_entry_slippage_bps,
            worst_exit_slippage_bps=s.worst_exit_slippage_bps,
            total_slippage_cost_inr=s.total_slippage_cost_inr,
            trade_count=s.trade_count,
        )
        for s in result.symbol_breakdown
    ]
    return SlippageResponse(
        avg_entry_slippage_bps=result.avg_entry_slippage_bps,
        avg_exit_slippage_bps=result.avg_exit_slippage_bps,
        worst_entry_slippage_bps=result.worst_entry_slippage_bps,
        worst_exit_slippage_bps=result.worst_exit_slippage_bps,
        total_slippage_cost_inr=result.total_slippage_cost_inr,
        symbol_breakdown=symbol_breakdown,
        sample_count=result.sample_count,
        trading_mode=result.trading_mode,
        from_date=from_date,
        to_date=to_date,
    )


def _percentiles_to_schema(p) -> LatencyPercentilesSchema:
    return LatencyPercentilesSchema(
        p50_ms=p.p50_ms,
        p95_ms=p.p95_ms,
        p99_ms=p.p99_ms,
        max_ms=p.max_ms,
    )


def _latency_to_response(result, from_date: datetime, to_date: datetime) -> LatencyResponse:
    ws_percentiles = (
        _percentiles_to_schema(result.ws_latency_percentiles)
        if result.ws_latency_percentiles is not None
        else None
    )
    return LatencyResponse(
        avg_signal_latency_ms=result.avg_signal_latency_ms,
        signal_latency_percentiles=_percentiles_to_schema(result.signal_latency_percentiles),
        avg_execution_latency_ms=result.avg_execution_latency_ms,
        execution_latency_percentiles=_percentiles_to_schema(result.execution_latency_percentiles),
        avg_ws_latency_ms=result.avg_ws_latency_ms,
        ws_latency_percentiles=ws_percentiles,
        sample_count=result.sample_count,
        high_latency_signals=result.high_latency_signals,
        from_date=from_date,
        to_date=to_date,
    )


def _mode_metrics_to_schema(m) -> ModeMetricsSchema:
    return ModeMetricsSchema(
        mode=m.mode,
        win_rate=m.win_rate,
        avg_pnl_per_trade=m.avg_pnl_per_trade,
        total_pnl=m.total_pnl,
        max_drawdown=m.max_drawdown,
        expectancy=m.expectancy,
        trade_count=m.trade_count,
        sharpe_ratio=m.sharpe_ratio,
    )


def _reality_gap_to_response(result, from_date: datetime, to_date: datetime) -> RealityGapResponse:
    return RealityGapResponse(
        backtest=_mode_metrics_to_schema(result.backtest) if result.backtest is not None else None,
        paper=_mode_metrics_to_schema(result.paper) if result.paper is not None else None,
        live=_mode_metrics_to_schema(result.live) if result.live is not None else None,
        paper_win_rate_gap=result.paper_win_rate_gap,
        paper_pnl_gap=result.paper_pnl_gap,
        paper_drawdown_gap=result.paper_drawdown_gap,
        paper_expectancy_gap=result.paper_expectancy_gap,
        live_win_rate_gap=result.live_win_rate_gap,
        live_pnl_gap=result.live_pnl_gap,
        live_drawdown_gap=result.live_drawdown_gap,
        live_expectancy_gap=result.live_expectancy_gap,
        live_vs_paper_win_rate_gap=result.live_vs_paper_win_rate_gap,
        live_vs_paper_pnl_gap=result.live_vs_paper_pnl_gap,
        strategy_id=result.strategy_id,
        analysis_period_days=result.analysis_period_days,
        from_date=from_date,
        to_date=to_date,
    )


def _health_result_to_schema(result) -> StrategyHealthSchema:
    dimensions = [
        HealthDimensionSchema(
            name=d.name,
            score=d.score,
            weight=d.weight,
            weighted_score=d.weighted_score,
            detail=d.detail,
        )
        for d in result.dimensions
    ]
    return StrategyHealthSchema(
        strategy_id=result.strategy_id,
        overall_score=result.overall_score,
        grade=result.grade,
        signal_quality_score=result.signal_quality_score,
        execution_quality_score=result.execution_quality_score,
        pnl_stability_score=result.pnl_stability_score,
        slippage_score=result.slippage_score,
        dimensions=dimensions,
        confidence=result.confidence,
        sample_trades=result.sample_trades,
        recommendation=result.recommendation,
    )
