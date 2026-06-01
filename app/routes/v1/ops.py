"""
Ops Dashboard API routes.

GET /api/v1/ops/health          — aggregate platform health + component breakdown
GET /api/v1/ops/components      — per-component health from DB (historical)
GET /api/v1/ops/incidents       — open and recent incidents
GET /api/v1/ops/risk-status     — live portfolio risk limits
GET /api/v1/ops/market-data     — market data feed health
GET /api/v1/ops/daily-report    — today's operational summary
POST /api/v1/ops/incidents/{id}/resolve   — manually resolve an incident
POST /api/v1/ops/incidents/{id}/escalate  — manually escalate an incident
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.monitoring.health_aggregator import health_aggregator
from app.monitoring.incident_manager import incident_manager
from app.monitoring.market_data_monitor import market_data_monitor
from app.monitoring.risk_monitor import risk_monitor
from app.monitoring.daily_report import daily_report_generator
from app.models.system_incident import IncidentStatus
from app.schemas.common import MessageResponse
from app.schemas.ops import (
    AggregateHealthResponse,
    ComponentHealthResponse,
    DailyReportResponse,
    IncidentListResponse,
    IncidentResponse,
    MarketDataStatusResponse,
    RiskLimitResponse,
    RiskStatusResponse,
)

router = APIRouter()


# ── Health ────────────────────────────────────────────────────────────────────

@router.get(
    "/health",
    response_model=AggregateHealthResponse,
    summary="Full platform health check",
    description=(
        "Runs all 7 component checks concurrently and returns aggregate status. "
        "Typical latency: 100–500ms depending on broker API response."
    ),
)
async def get_aggregate_health() -> AggregateHealthResponse:
    report = await health_aggregator.run_all()
    components = [
        ComponentHealthResponse(
            component_name=c.component_name,
            status=c.status,
            last_heartbeat=c.checked_at if c.healthy else None,
            latency_ms=c.latency_ms,
            error_count=c.error_count,
            error_message=c.error_message,
            metadata=c.metadata,
            updated_at=c.checked_at,
        )
        for c in report.components
    ]
    return AggregateHealthResponse(
        overall_status=report.overall_status,
        components=components,
        open_incident_count=report.open_incident_count,
        healthy_count=report.healthy_count,
        degraded_count=report.degraded_count,
        unhealthy_count=report.unhealthy_count,
        generated_at=report.generated_at,
    )


@router.get(
    "/components",
    response_model=list[ComponentHealthResponse],
    summary="Per-component health from DB (cached)",
    description="Returns last-persisted health state for each component without re-running checks.",
)
async def get_component_statuses() -> list[ComponentHealthResponse]:
    from app.repositories.system_health_status_repository import SystemHealthStatusRepository
    statuses = await SystemHealthStatusRepository().get_all()
    return [
        ComponentHealthResponse(
            component_name=s.component_name,
            status=s.status.value,
            last_heartbeat=s.last_heartbeat,
            latency_ms=s.latency_ms,
            error_count=s.error_count,
            error_message=s.error_message,
            metadata=s.metadata,
            updated_at=s.updated_at,
        )
        for s in statuses
    ]


# ── Incidents ─────────────────────────────────────────────────────────────────

@router.get(
    "/incidents",
    response_model=IncidentListResponse,
    summary="List incidents",
    description="Returns recent incidents. Filter by component or status.",
)
async def list_incidents(
    component: Optional[str] = Query(default=None),
    open_only: bool = Query(default=False, description="Return only OPEN/INVESTIGATING incidents"),
    limit: int = Query(default=50, ge=1, le=200),
) -> IncidentListResponse:
    if open_only:
        items = await incident_manager.list_open(component)
    else:
        items = await incident_manager.list_recent(limit)
        if component:
            items = [i for i in items if i.component == component]

    open_count = sum(
        1 for i in items
        if i.status in (IncidentStatus.OPEN, IncidentStatus.INVESTIGATING)
    )
    responses = [
        IncidentResponse(
            incident_id=i.incident_id,
            severity=i.severity,
            component=i.component,
            description=i.description,
            detected_at=i.detected_at,
            resolved_at=i.resolved_at,
            status=i.status,
            timeline=i.timeline,
            metadata=i.metadata,
            created_at=i.created_at,
        )
        for i in items
    ]
    return IncidentListResponse(items=responses, total=len(responses), open_count=open_count)


@router.post(
    "/incidents/{incident_id}/resolve",
    response_model=MessageResponse,
    summary="Manually resolve an incident",
)
async def resolve_incident(incident_id: str) -> MessageResponse:
    result = await incident_manager.resolve(incident_id, "Manually resolved via ops API.")
    if result is None:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")
    return MessageResponse(message=f"Incident {incident_id} resolved.")


@router.post(
    "/incidents/{incident_id}/escalate",
    response_model=MessageResponse,
    summary="Manually escalate an incident to CRITICAL",
)
async def escalate_incident(incident_id: str) -> MessageResponse:
    result = await incident_manager.escalate(incident_id, "Manually escalated via ops API.")
    if result is None:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")
    return MessageResponse(message=f"Incident {incident_id} escalated to CRITICAL.")


# ── Risk status ───────────────────────────────────────────────────────────────

@router.get(
    "/risk-status",
    response_model=RiskStatusResponse,
    summary="Live portfolio risk limits",
    description="Shows current capital utilisation, daily loss, and exposure vs configured limits.",
)
async def get_risk_status() -> RiskStatusResponse:
    report = await risk_monitor.check()
    return RiskStatusResponse(
        total_capital=report.total_capital,
        used_capital=report.used_capital,
        available_capital=report.available_capital,
        daily_pnl=report.daily_pnl,
        open_positions=report.open_positions,
        is_halted=report.is_halted,
        halt_reason=report.halt_reason,
        limit_checks=[
            RiskLimitResponse(
                name=c.name, current=c.current, limit=c.limit,
                breached=c.breached, warning=c.warning, pct_used=c.pct_used,
            )
            for c in report.limit_checks
        ],
        strategy_exposure=report.strategy_exposure,
        sector_exposure=report.sector_exposure,
        any_breached=report.any_breached,
        any_warning=report.any_warning,
        notes=report.notes,
        checked_at=report.checked_at,
    )


# ── Market data ───────────────────────────────────────────────────────────────

@router.get(
    "/market-data",
    response_model=MarketDataStatusResponse,
    summary="Market data feed health",
)
async def get_market_data_status() -> MarketDataStatusResponse:
    report = await market_data_monitor.check()
    return MarketDataStatusResponse(
        feed_status=report.feed_status,
        ticks_received=report.ticks_received,
        ticks_dropped=report.ticks_dropped,
        candles_emitted=report.candles_emitted,
        reconnect_count=report.reconnect_count,
        seconds_since_last_tick=report.seconds_since_last_tick,
        seconds_since_last_candle=report.seconds_since_last_candle,
        watchlist_size=report.watchlist_size,
        stale_symbols=report.stale_symbols,
        notes=report.notes,
        checked_at=report.checked_at,
    )


# ── Daily report ──────────────────────────────────────────────────────────────

@router.get(
    "/daily-report",
    response_model=DailyReportResponse,
    summary="Daily operational summary",
    description="EOD summary: signals, trades, P&L, incidents, alerts.",
)
async def get_daily_report(
    trading_date: Optional[date] = Query(
        default=None,
        description="Date to report on. Defaults to today.",
    ),
) -> DailyReportResponse:
    report = await daily_report_generator.generate(trading_date)
    return DailyReportResponse(
        trading_date=report.trading_date,
        generated_at=report.generated_at,
        broker_reconnects=report.broker_reconnects,
        broker_session_healthy=report.broker_session_healthy,
        signals_generated=report.signals_generated,
        signals_approved=report.signals_approved,
        signals_rejected=report.signals_rejected,
        paper_trades=report.paper_trades,
        paper_pnl=report.paper_pnl,
        live_trades=report.live_trades,
        live_pnl=report.live_pnl,
        portfolio_halted=report.portfolio_halted,
        halt_reason=report.halt_reason,
        max_exposure_reached_pct=report.max_exposure_reached_pct,
        open_incidents=report.open_incidents,
        resolved_incidents=report.resolved_incidents,
        critical_incidents=report.critical_incidents,
        total_alerts_fired=report.total_alerts_fired,
        undelivered_alerts=report.undelivered_alerts,
        notes=report.notes,
    )
