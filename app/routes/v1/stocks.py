"""
Stock management API routes.

GET  /api/v1/stocks                  — Paginated list of all stocks
GET  /api/v1/stocks/{symbol}         — Single stock detail
GET  /api/v1/stocks/{symbol}/candles — Historical candles for a stock
POST /api/v1/stocks/initialize       — Seed DB with NIFTY50 universe
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.exceptions import DocumentNotFoundException
from app.models.stock import Stock
from app.schemas.candle import CandleBucketResponse, CandleDataResponse
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.stock import StockCreate, StockListItem, StockResponse
from app.services.historical_data_service import HistoricalDataService
from app.services.stock_universe_service import StockUniverseService
from app.utils.candle_intervals import CandleInterval
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight

router = APIRouter()
logger = get_logger(__name__)

_universe_svc = StockUniverseService()
_data_svc = HistoricalDataService()


# ── List stocks ───────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=PaginatedResponse[StockListItem],
    summary="List all stocks",
)
async def list_stocks(
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(default=50, ge=1, le=200, description="Records per page"),
    index: Optional[str] = Query(None, description="Filter by index, e.g. 'NIFTY50'"),
    active_only: bool = Query(default=True, description="Only return active stocks"),
) -> PaginatedResponse[StockListItem]:
    """
    Return a paginated list of registered stocks.

    Use `index=NIFTY50` to filter by index membership.
    """
    stocks = await _universe_svc.get_active_stocks(index=index if active_only else None)
    if not active_only:
        from app.repositories.stock_repository import StockRepository
        repo = StockRepository()
        stocks = await repo.get_all()

    total = len(stocks)
    skip = (page - 1) * page_size
    page_stocks = stocks[skip : skip + page_size]

    items = [
        StockListItem(
            symbol=s.symbol,
            exchange=s.exchange,
            company_name=s.company_name,
            is_active=s.is_active,
            indices=s.indices,
        )
        for s in page_stocks
    ]
    return PaginatedResponse.build(items=items, total=total, page=page, page_size=page_size)


# ── Single stock detail ───────────────────────────────────────────────────────

@router.get(
    "/{symbol}",
    response_model=StockResponse,
    summary="Get stock details",
)
async def get_stock(symbol: str) -> StockResponse:
    """Return full details for a single stock by its ticker symbol."""
    stock = await _universe_svc.get_stock(symbol.upper())
    if stock is None:
        raise DocumentNotFoundException("Stock", symbol.upper())

    return StockResponse(
        symbol=stock.symbol,
        exchange=stock.exchange,
        instrument_token=stock.instrument_token,
        company_name=stock.company_name,
        indices=stock.indices,
        sector=stock.sector,
        is_active=stock.is_active,
        created_at=stock.created_at,
        updated_at=stock.updated_at,
    )


# ── Historical candles for a stock ───────────────────────────────────────────

@router.get(
    "/{symbol}/candles",
    response_model=list[CandleDataResponse],
    summary="Get historical candles for a stock",
)
async def get_candles(
    symbol: str,
    from_date: str = Query(..., description="Start date YYYY-MM-DD"),
    to_date: str = Query(..., description="End date YYYY-MM-DD"),
    interval: str = Query(default="FIFTEEN_MINUTE", description="CandleInterval value"),
    limit: int = Query(default=500, ge=1, le=5000, description="Max candles to return"),
) -> list[CandleDataResponse]:
    """
    Return a flat, time-ordered list of OHLCV candles for a stock.

    The response unpacks the MongoDB day-bucket storage into a plain
    candle list that strategies and the dashboard can consume directly.
    """
    # Validate symbol exists.
    stock = await _universe_svc.get_stock(symbol.upper())
    if stock is None:
        raise DocumentNotFoundException("Stock", symbol.upper())

    try:
        from_d = date.fromisoformat(from_date)
        to_d = date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Dates must be YYYY-MM-DD format.")

    try:
        interval_enum = CandleInterval(interval)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid interval '{interval}'. Valid values: {[i.value for i in CandleInterval]}",
        )

    candles = await _data_svc.get_candles_for_strategy(
        symbol=symbol.upper(),
        from_date=from_d,
        to_date=to_d,
        interval=interval_enum,
    )

    # Apply limit from the end (most-recent candles if too many).
    if len(candles) > limit:
        candles = candles[-limit:]

    return [
        CandleDataResponse(
            time=c.time,
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume,
        )
        for c in candles
    ]


# ── Seed NIFTY50 universe ─────────────────────────────────────────────────────

@router.post(
    "/initialize",
    response_model=MessageResponse,
    summary="Seed database with NIFTY50 stock universe",
)
async def initialize_universe(
    index: str = Query(default="NIFTY50", description="Universe to initialise"),
) -> MessageResponse:
    """
    Seed MongoDB with the static stock universe for the specified index.

    Idempotent — existing stocks are not duplicated.
    """
    inserted = await _universe_svc.initialise_universe(index=index)
    counts = await _universe_svc.get_stock_count()
    return MessageResponse(
        message=(
            f"Universe '{index}' initialised. "
            f"{inserted} new stocks added. "
            f"Total active: {counts['active']}."
        )
    )
