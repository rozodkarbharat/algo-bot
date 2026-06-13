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

from pydantic import BaseModel, Field

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
    search: Optional[str] = Query(
        None, description="Case-insensitive match on symbol or company name"
    ),
) -> PaginatedResponse[StockListItem]:
    """
    Return a paginated list of registered stocks.

    Use `index=NIFTY50` to filter by index membership and `search=` to match by
    symbol or company name across the whole universe (not just the current page).
    """
    stocks = await _universe_svc.get_active_stocks(index=index if active_only else None)
    if not active_only:
        from app.repositories.stock_repository import StockRepository
        repo = StockRepository()
        stocks = await repo.get_all()

    if search:
        q = search.strip().lower()
        stocks = [
            s
            for s in stocks
            if q in s.symbol.lower() or q in (s.company_name or "").lower()
        ]

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


# ── Seed from NSE index (NIFTY100/200/500) ────────────────────────────────────

class SeedFromIndexResponse(BaseModel):
    """Result of seeding the universe from an NSE index list."""

    index: str
    total_symbols: int = Field(..., description="Symbols returned by NSE for this index")
    inserted: int = Field(..., description="Brand-new stocks added to the DB")
    updated: int = Field(..., description="Existing stocks re-tagged with this index")
    matched: int = Field(..., description="Symbols resolved via Angel One scrip master")
    unmatched: list[str] = Field(
        default_factory=list,
        description="Index symbols that could not be matched in the Angel One scrip master",
    )
    active_total: int = Field(..., description="Current count of active stocks in the DB")


@router.post(
    "/seed-universe",
    response_model=SeedFromIndexResponse,
    summary="Seed DB from a live NSE index (NIFTY100/NIFTY200/NIFTY500)",
)
async def seed_universe_from_index(
    index: str = Query(
        default="NIFTY500",
        description="NSE index whose constituents to pull (e.g. NIFTY500).",
    ),
    force_refresh: bool = Query(
        default=False,
        description="Re-download NSE CSV + Angel One scrip master, ignoring on-disk cache.",
    ),
) -> SeedFromIndexResponse:
    """
    Build the active stock universe from a live NSE index list.

    The handler:
      1. Downloads (or re-uses cached) NSE constituent CSV for the index.
      2. Downloads (or re-uses cached) Angel One scrip master JSON.
      3. Upserts each constituent into the ``stocks`` collection with the
         correct ``instrument_token`` and tags it with the index name.

    Run this once to bootstrap the universe (or after a quarterly NSE rebalance).
    """
    if force_refresh:
        from app.brokers.angelone.scrip_master import scrip_master as _scrip
        await _scrip.refresh(force=True)

    try:
        result = await _universe_svc.seed_universe_from_index(
            index=index, force_refresh=force_refresh,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    counts = await _universe_svc.get_stock_count()
    matched = result.total_symbols - len(result.unmatched)
    return SeedFromIndexResponse(
        index=result.index,
        total_symbols=result.total_symbols,
        inserted=result.inserted,
        updated=result.updated,
        matched=matched,
        unmatched=result.unmatched,
        active_total=counts["active"],
    )
