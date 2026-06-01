"""
Data synchronisation API routes.

POST /api/v1/sync/historical-data — Trigger historical candle ingestion
GET  /api/v1/sync/logs            — Paginated sync audit log
GET  /api/v1/sync/logs/{symbol}   — Most recent log for a specific symbol
GET  /api/v1/sync/status          — Quick summary of sync health
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.market_data_sync_log import SyncStatus
from app.repositories.market_data_sync_log_repository import MarketDataSyncLogRepository
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.sync import HistoricalSyncRequest, SyncLogResponse, SyncResultResponse
from app.services.historical_data_service import HistoricalDataService
from app.utils.candle_intervals import CandleInterval
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

_data_svc = HistoricalDataService()
_log_repo = MarketDataSyncLogRepository()


# ── Trigger ingestion ─────────────────────────────────────────────────────────

@router.post(
    "/historical-data",
    response_model=SyncResultResponse,
    summary="Trigger historical candle ingestion",
)
async def sync_historical_data(body: HistoricalSyncRequest) -> SyncResultResponse:
    """
    Start a historical data ingestion job.

    - `from_date` / `to_date`: YYYY-MM-DD range to sync.
    - `symbols`: Optional list of tickers; omit to sync all active NIFTY50 stocks.
    - `force_refetch`: Re-download dates that already exist in MongoDB.

    This is a **synchronous** endpoint — it waits for ingestion to finish
    before returning. For large date ranges, use the scheduler jobs instead.
    """
    try:
        from_d = date.fromisoformat(body.from_date)
        to_d = date.fromisoformat(body.to_date)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="Dates must be in YYYY-MM-DD format."
        )

    if from_d > to_d:
        raise HTTPException(
            status_code=422, detail="from_date must be before or equal to to_date."
        )

    try:
        interval = CandleInterval(body.interval)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid interval '{body.interval}'. Valid: {[i.value for i in CandleInterval]}",
        )

    logger.info(
        "Manual sync triggered via API: %s → %s, interval=%s, symbols=%s",
        from_d, to_d, interval, body.symbols or "all",
    )

    result = await _data_svc.sync_historical_data(
        from_date=from_d,
        to_date=to_d,
        interval=interval,
        symbols=body.symbols,
        force_refetch=body.force_refetch,
    )

    return SyncResultResponse(**result.to_dict())


# ── Sync logs ─────────────────────────────────────────────────────────────────

@router.get(
    "/logs",
    response_model=PaginatedResponse[SyncLogResponse],
    summary="Paginated sync audit logs",
)
async def list_sync_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status: Optional[str] = Query(
        None, description="Filter by status: PENDING, RUNNING, SUCCESS, PARTIAL, FAILED, SKIPPED"
    ),
) -> PaginatedResponse[SyncLogResponse]:
    """Return recent sync audit log entries, newest first."""
    status_filter: Optional[SyncStatus] = None
    if status:
        try:
            status_filter = SyncStatus(status.upper())
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Valid: {[s.value for s in SyncStatus]}",
            )

    skip = (page - 1) * page_size
    logs = await _log_repo.get_recent_logs(
        limit=page_size, skip=skip, status=status_filter
    )
    total = await _log_repo.count()

    items = [
        SyncLogResponse(
            id=str(log.id) if log.id else None,
            symbol=log.symbol,
            exchange=log.exchange,
            interval=log.interval,
            sync_from=log.sync_from,
            sync_to=log.sync_to,
            sync_end=log.sync_end,
            records_inserted=log.records_inserted,
            records_skipped=log.records_skipped,
            status=log.status,
            error_message=log.error_message,
            created_at=log.created_at,
        )
        for log in logs
    ]
    return PaginatedResponse.build(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/logs/{symbol}",
    response_model=SyncLogResponse,
    summary="Latest sync log for a specific symbol",
)
async def get_symbol_sync_log(
    symbol: str,
    interval: str = Query(default="FIFTEEN_MINUTE"),
) -> SyncLogResponse:
    """Return the most recent sync audit log entry for a given symbol."""
    log = await _log_repo.get_latest_log_for_symbol(
        symbol=symbol.upper(), interval=interval
    )
    if log is None:
        raise HTTPException(
            status_code=404,
            detail=f"No sync log found for {symbol.upper()} [{interval}].",
        )
    return SyncLogResponse(
        id=str(log.id) if log.id else None,
        symbol=log.symbol,
        exchange=log.exchange,
        interval=log.interval,
        sync_from=log.sync_from,
        sync_to=log.sync_to,
        sync_end=log.sync_end,
        records_inserted=log.records_inserted,
        records_skipped=log.records_skipped,
        status=log.status,
        error_message=log.error_message,
        created_at=log.created_at,
    )


# ── Sync status summary ───────────────────────────────────────────────────────

@router.get(
    "/status",
    summary="Sync health summary",
)
async def sync_status() -> dict:
    """Return a quick count of logs per status for dashboard monitoring."""
    counts = {
        status.value: await _log_repo.count_by_status(status)
        for status in SyncStatus
    }
    total = await _log_repo.count()
    return {"total": total, "by_status": counts}
