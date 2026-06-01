"""
Paper trading API routes.

GET  /api/v1/paper/account     — account state (capital + tallies)
GET  /api/v1/paper/positions   — list paper positions (paginated)
GET  /api/v1/paper/trades      — list closed trades (paginated)
GET  /api/v1/paper/pnl         — live PnL snapshot
POST /api/v1/paper/reset       — reset daily counters (does not close positions)
POST /api/v1/paper/hard-reset  — wipe account back to starting capital
POST /api/v1/paper/pause       — pause paper trading
POST /api/v1/paper/resume      — resume paper trading
POST /api/v1/paper/close-all   — force-close every open position

Routes delegate to PaperTradingService only — no direct repository access.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Query

from app.schemas.common import PaginatedResponse
from app.schemas.paper import (
    PaperAccountResponse,
    PaperPauseRequest,
    PaperPauseResponse,
    PaperPnLResponse,
    PaperPositionResponse,
    PaperResetResponse,
    PaperTradeResponse,
)
from app.services.paper_trading_service import paper_trading_service
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ── Account ──────────────────────────────────────────────────────────────────

@router.get(
    "/account",
    response_model=PaperAccountResponse,
    summary="Get paper-trading account state",
)
async def get_account() -> PaperAccountResponse:
    account = await paper_trading_service.get_account()
    return PaperAccountResponse.from_document(account)


# ── Positions ────────────────────────────────────────────────────────────────

@router.get(
    "/positions",
    response_model=PaginatedResponse[PaperPositionResponse],
    summary="List paper positions (open + closed)",
)
async def list_positions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    open_only: bool = Query(
        default=False,
        description="When true, return only currently open positions",
    ),
) -> PaginatedResponse[PaperPositionResponse]:
    if open_only:
        positions = await paper_trading_service.list_open_positions()
        total = len(positions)
        skip = (page - 1) * page_size
        paged = positions[skip : skip + page_size]
    else:
        # Pull a window large enough for the requested page; clients
        # paginating deep should use the ?open_only=true variant or
        # filter server-side by trading_date in a future extension.
        skip = (page - 1) * page_size
        positions = await paper_trading_service.list_positions(
            limit=skip + page_size, skip=0
        )
        total = len(positions)
        paged = positions[skip : skip + page_size]

    items = [PaperPositionResponse.from_document(p) for p in paged]
    return PaginatedResponse.build(
        items=items, total=total, page=page, page_size=page_size
    )


# ── Trades ───────────────────────────────────────────────────────────────────

@router.get(
    "/trades",
    response_model=PaginatedResponse[PaperTradeResponse],
    summary="List closed paper trades (audit ledger)",
)
async def list_trades(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> PaginatedResponse[PaperTradeResponse]:
    skip = (page - 1) * page_size
    trades = await paper_trading_service.list_recent_trades(
        limit=skip + page_size, skip=0
    )
    total = len(trades)
    paged = trades[skip : skip + page_size]
    items = [PaperTradeResponse.from_document(t) for t in paged]
    return PaginatedResponse.build(
        items=items, total=total, page=page, page_size=page_size
    )


# ── PnL ──────────────────────────────────────────────────────────────────────

@router.get(
    "/pnl",
    response_model=PaperPnLResponse,
    summary="Live PnL snapshot",
)
async def get_pnl() -> PaperPnLResponse:
    snap = await paper_trading_service.pnl_snapshot()
    return PaperPnLResponse(**snap)


# ── Lifecycle controls ───────────────────────────────────────────────────────

@router.post(
    "/reset",
    response_model=PaperResetResponse,
    summary="Reset daily counters (does NOT close open positions)",
)
async def reset_daily(
    trading_date: Optional[date] = Query(
        default=None,
        description="Trading date for the reset (defaults to today IST)",
    ),
) -> PaperResetResponse:
    account = await paper_trading_service.reset_daily(trading_date=trading_date)
    return PaperResetResponse(
        account_id=account.account_id,
        starting_capital=account.starting_capital,
        available_capital=account.available_capital,
        message="Daily counters reset; lifetime totals preserved.",
    )


@router.post(
    "/hard-reset",
    response_model=PaperResetResponse,
    summary="Wipe the paper account back to starting capital",
)
async def hard_reset() -> PaperResetResponse:
    account = await paper_trading_service.hard_reset()
    return PaperResetResponse(
        account_id=account.account_id,
        starting_capital=account.starting_capital,
        available_capital=account.available_capital,
        message="Account reset to starting capital. Open positions closed; trade history preserved.",
    )


@router.post(
    "/pause",
    response_model=PaperPauseResponse,
    summary="Pause paper trading",
)
async def pause(body: Optional[PaperPauseRequest] = None) -> PaperPauseResponse:
    reason = (body.reason if body is not None else None) or "manual_pause"
    account = await paper_trading_service.pause(reason=reason)
    return PaperPauseResponse(
        is_paused=account.is_paused,
        pause_reason=account.pause_reason,
        message="Paper trading paused.",
    )


@router.post(
    "/resume",
    response_model=PaperPauseResponse,
    summary="Resume paper trading",
)
async def resume() -> PaperPauseResponse:
    account = await paper_trading_service.resume()
    return PaperPauseResponse(
        is_paused=account.is_paused,
        pause_reason=account.pause_reason,
        message="Paper trading resumed.",
    )


@router.post(
    "/close-all",
    summary="Force-close every open paper position",
)
async def close_all() -> dict:
    result = await paper_trading_service.close_all_open()
    return {"closed": result.closed, "reason": result.reason}
