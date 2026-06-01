"""
Live signal engine API routes.

GET  /api/v1/live/signals               — recent live signals (paginated)
GET  /api/v1/live/signals/{symbol}      — signal history for a single symbol
GET  /api/v1/live/market-state          — intraday state for today's shortlist
POST /api/v1/live/start                 — manually start the live engine
POST /api/v1/live/stop                  — manually stop the live engine
GET  /api/v1/live/status                — high-level engine status

Routes call services only — no direct repository access here. The live engine
itself is owned by the LiveSignalService singleton.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.exceptions import DocumentNotFoundException
from app.repositories.intraday_market_state_repository import (
    IntradayMarketStateRepository,
)
from app.repositories.live_signal_repository import LiveSignalRepository
from app.schemas.common import PaginatedResponse
from app.live.health_monitor import live_health_monitor
from app.schemas.live import (
    IntradayMarketStateResponse,
    LiveEngineStatusResponse,
    LiveHealthResponse,
    LiveSignalResponse,
    StartLiveEngineResponse,
    StopLiveEngineResponse,
)
from app.services.live_signal_service import live_signal_service
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight
from app.utils.trading_day import today_ist

router = APIRouter()
logger = get_logger(__name__)

_signal_repo = LiveSignalRepository()
_state_repo = IntradayMarketStateRepository()


# ── Live signals ──────────────────────────────────────────────────────────────

@router.get(
    "/signals",
    response_model=PaginatedResponse[LiveSignalResponse],
    summary="List live signals (paginated)",
)
async def list_live_signals(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    trading_date: Optional[date] = Query(
        default=None, description="Filter by trading date (defaults to today)"
    ),
) -> PaginatedResponse[LiveSignalResponse]:
    """
    Return live signals, newest first.

    With `trading_date` set, returns just that day's signals (sorted by
    breakout_time). Without it, returns the global newest-first feed.
    """
    skip = (page - 1) * page_size

    if trading_date is not None:
        records = await _signal_repo.get_for_date(date_to_utc_midnight(trading_date))
        records = list(reversed(records))  # newest first within the day
    else:
        # Pull enough to satisfy the current page; large pages should use
        # a date filter to avoid scanning the full collection.
        records = await _signal_repo.list_recent(limit=skip + page_size, skip=0)

    total = len(records)
    paged = records[skip : skip + page_size]
    items = [LiveSignalResponse.from_document(r) for r in paged]
    return PaginatedResponse.build(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/signals/{symbol}",
    response_model=PaginatedResponse[LiveSignalResponse],
    summary="Live-signal history for a symbol",
)
async def get_live_signals_for_symbol(
    symbol: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> PaginatedResponse[LiveSignalResponse]:
    """Return historical live signals for a symbol, newest first."""
    symbol = symbol.upper()
    skip = (page - 1) * page_size
    # Fetch a window large enough to compute total within reason. For deeper
    # history, the client should paginate. This mirrors the analysis route.
    records = await _signal_repo.get_history_for_symbol(symbol, limit=10_000, skip=0)
    total = len(records)
    paged = records[skip : skip + page_size]
    items = [LiveSignalResponse.from_document(r) for r in paged]
    return PaginatedResponse.build(items=items, total=total, page=page, page_size=page_size)


# ── Intraday market state ─────────────────────────────────────────────────────

@router.get(
    "/market-state",
    response_model=list[IntradayMarketStateResponse],
    summary="Today's intraday state per symbol",
)
async def get_market_state(
    trading_date: Optional[date] = Query(default=None),
) -> list[IntradayMarketStateResponse]:
    """
    Return the IntradayMarketState rows for `trading_date` (today by default).

    Each row exposes ORB capture, breakout status, signal-generated flag and
    trade-locked flag — useful for the dashboard's live overview.
    """
    d = trading_date or today_ist()
    rows = await _state_repo.get_for_date(date_to_utc_midnight(d))
    return [IntradayMarketStateResponse.from_document(r) for r in rows]


# ── Engine control ────────────────────────────────────────────────────────────

@router.post(
    "/start",
    response_model=StartLiveEngineResponse,
    summary="Start the live signal engine",
)
async def start_live_engine(
    target_date: Optional[date] = Query(
        default=None,
        description="Target trading date (defaults to today)",
    ),
) -> StartLiveEngineResponse:
    """
    Manually start the live engine for today's session.

    The scheduler will normally call this at 09:15 IST. Manual invocation is
    useful for warm-starts after a deploy mid-session.
    """
    result = await live_signal_service.start(target_date=target_date)
    return StartLiveEngineResponse(
        started=result.started,
        subscribed_symbols=result.subscribed_symbols,
        trading_date=result.trading_date,
        message=result.message,
    )


@router.post(
    "/stop",
    response_model=StopLiveEngineResponse,
    summary="Stop the live signal engine",
)
async def stop_live_engine() -> StopLiveEngineResponse:
    """Stop the live engine — does NOT delete intraday state."""
    result = await live_signal_service.stop()
    return StopLiveEngineResponse(
        stopped=result.stopped,
        signals_generated=result.signals_generated,
        duration_seconds=result.duration_seconds,
        message=result.message,
    )


@router.get(
    "/status",
    response_model=LiveEngineStatusResponse,
    summary="Live engine high-level status",
)
async def live_engine_status() -> LiveEngineStatusResponse:
    snapshot = await live_signal_service.status_snapshot()
    return LiveEngineStatusResponse(**snapshot)


@router.get(
    "/health",
    response_model=LiveHealthResponse,
    summary="Live engine health snapshot",
)
async def live_engine_health() -> LiveHealthResponse:
    """
    Detailed health view of the live engine — staleness, reconnects, drops.

    Use this for dashboard alerting; `/status` is the lightweight summary.
    """
    snap = live_health_monitor.evaluate()
    return LiveHealthResponse(
        status=snap.status.value,
        running=snap.running,
        market_open=snap.market_open,
        entry_window_open=snap.entry_window_open,
        reconnect_count=snap.reconnect_count,
        ticks_received=snap.ticks_received,
        ticks_dropped=snap.ticks_dropped,
        candles_emitted=snap.candles_emitted,
        signals_emitted=snap.signals_emitted,
        last_tick_at=snap.last_tick_at,
        last_candle_at=snap.last_candle_at,
        seconds_since_last_tick=snap.seconds_since_last_tick,
        seconds_since_last_candle=snap.seconds_since_last_candle,
        watchlist_size=snap.watchlist_size,
        stale_symbols=snap.stale_symbols,
        notes=snap.notes,
    )
