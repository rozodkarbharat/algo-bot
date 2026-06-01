"""
Pydantic v2 schemas for the Ops Dashboard API.

These are HTTP response models only — NOT Beanie documents.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.system_health_status import ComponentStatus
from app.models.system_incident import IncidentStatus
from app.models.alert_event import AlertSeverity


# ── Component health ──────────────────────────────────────────────────────────

class ComponentHealthResponse(BaseModel):
    component_name: str
    status: str                   # "healthy" | "degraded" | "unhealthy" | "unknown"
    last_heartbeat: Optional[datetime]
    latency_ms: float
    error_count: int
    error_message: Optional[str]
    metadata: dict[str, Any]
    updated_at: datetime

    model_config = {"from_attributes": True}


class AggregateHealthResponse(BaseModel):
    overall_status: str           # "healthy" | "degraded" | "unhealthy"
    components: list[ComponentHealthResponse]
    open_incident_count: int
    healthy_count: int
    degraded_count: int
    unhealthy_count: int
    generated_at: datetime


# ── Incidents ─────────────────────────────────────────────────────────────────

class IncidentResponse(BaseModel):
    incident_id: str
    severity: AlertSeverity
    component: str
    description: str
    detected_at: datetime
    resolved_at: Optional[datetime]
    status: IncidentStatus
    timeline: list[dict]
    metadata: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class IncidentListResponse(BaseModel):
    items: list[IncidentResponse]
    total: int
    open_count: int


# ── Risk status ───────────────────────────────────────────────────────────────

class RiskLimitResponse(BaseModel):
    name: str
    current: float
    limit: float
    breached: bool
    warning: bool
    pct_used: float


class RiskStatusResponse(BaseModel):
    total_capital: float
    used_capital: float
    available_capital: float
    daily_pnl: float
    open_positions: int
    is_halted: bool
    halt_reason: Optional[str]
    limit_checks: list[RiskLimitResponse]
    strategy_exposure: dict
    sector_exposure: dict
    any_breached: bool
    any_warning: bool
    notes: list[str]
    checked_at: datetime


# ── Daily report ──────────────────────────────────────────────────────────────

class DailyReportResponse(BaseModel):
    trading_date: date
    generated_at: datetime

    broker_reconnects: int
    broker_session_healthy: bool

    signals_generated: int
    signals_approved: int
    signals_rejected: int

    paper_trades: int
    paper_pnl: float
    live_trades: int
    live_pnl: float

    portfolio_halted: bool
    halt_reason: Optional[str]
    max_exposure_reached_pct: float

    open_incidents: int
    resolved_incidents: int
    critical_incidents: int

    total_alerts_fired: int
    undelivered_alerts: int

    notes: list[str]


# ── Market data status ────────────────────────────────────────────────────────

class MarketDataStatusResponse(BaseModel):
    feed_status: str
    ticks_received: int
    ticks_dropped: int
    candles_emitted: int
    reconnect_count: int
    seconds_since_last_tick: Optional[float]
    seconds_since_last_candle: Optional[float]
    watchlist_size: int
    stale_symbols: list[str]
    notes: list[str]
    checked_at: datetime
